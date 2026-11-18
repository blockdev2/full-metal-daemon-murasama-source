"""Crash safety: a write-ahead journal of every translation and a checkpoint of the running queue.

Order on every save: journal line (flushed to disk) -> scene file (atomic rename) -> checkpoint.
If the app dies anywhere in between, replay() re-applies journal entries missing from the scenes.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .core import Scene, atomic_write

JOURNAL_PATH = Path('translator_journal.jsonl')
STATE_PATH = Path('translator_state.json')


def now() -> str:
    return datetime.now().isoformat(timespec='seconds')


class Journal:
    def __init__(self, path: Path = JOURNAL_PATH):
        self.path = path

    def append(self, scene: str, entries: list[tuple[int, str, str]]) -> None:
        """entries: (row index, en, pt). Flushed and fsynced before returning."""
        with open(self.path, 'a', encoding='utf-8') as f:
            for idx, en, pt in entries:
                f.write(json.dumps({'t': now(), 'scene': scene, 'index': idx, 'en': en, 'pt': pt},
                                   ensure_ascii=False) + '\n')
            f.flush()
            os.fsync(f.fileno())

    def entries(self):
        if not self.path.exists():
            return
        with open(self.path, encoding='utf-8') as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue  # a line cut by a crash mid-write: everything before it is intact

    def count(self) -> int:
        return sum(1 for _ in self.entries())

    def size(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    def replay(self, scenes: list[Scene]) -> int:
        """Re-applies journaled translations that never reached their scene file. Returns how many."""
        by_name = {s.name: s for s in scenes}
        touched, restored = set(), 0
        for e in self.entries():
            s = by_name.get(e.get('scene'))
            if not s or not isinstance(e.get('index'), int) or e['index'] >= len(s.rows):
                continue
            row = s.rows[e['index']]
            if not row.pt and row.en == e.get('en') and e.get('pt'):  # en must match: never the wrong line
                s.set_pt(row, e['pt'])
                touched.add(s.name)
                restored += 1
        for name in touched:
            by_name[name].save()
        return restored

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


@dataclass
class Checkpoint:
    queue: list[str] = field(default_factory=list)  # scenes still to do in the running session
    current: str = ''
    started: str = ''
    updated: str = ''
    strings_done: int = 0  # translated during this session
    failed: dict[str, list[int]] = field(default_factory=dict)  # scene -> row indices that kept failing

    @property
    def interrupted(self) -> bool:
        return bool(self.queue)

    @property
    def failed_count(self) -> int:
        return sum(len(v) for v in self.failed.values())

    @classmethod
    def load(cls, path: Path = STATE_PATH) -> 'Checkpoint':
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path = STATE_PATH) -> None:
        self.updated = now()
        atomic_write(path, json.dumps(asdict(self), ensure_ascii=False, indent=2))

    def begin(self, names: list[str]) -> None:
        self.queue, self.current, self.started, self.strings_done = list(names), '', now(), 0

    def mark_failed(self, scene: str, indices: list[int]) -> None:
        cur = set(self.failed.get(scene, [])) | set(indices)
        self.failed[scene] = sorted(cur)

    def mark_done(self, scene: str, indices: list[int]) -> None:
        if scene in self.failed:
            left = sorted(set(self.failed[scene]) - set(indices))
            if left:
                self.failed[scene] = left
            else:
                del self.failed[scene]

    def finish_scene(self, scene: str) -> None:
        if scene in self.queue:
            self.queue.remove(scene)
        self.current = ''
