"""Domain logic: scene files, what gets translated, prompts and response validation.

A scene is one texto/*.json file: a list of rows {"offset", "en", "pt"}. Each "en" is a block of
lines separated by CRLF. Lines starting with "//" are the original Japanese (kept as is); the other
non-blank lines are the English text to translate. "pt" receives the same block with only the
English lines replaced, so inject_text.py can write it back into the script.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

LINE_SEP = re.compile(r'(\r\n|\n)')
TAG = re.compile(r'<[^>]*>')
# <br> is a manual line break placed for English word wrapping: its position is meaningless in pt-BR
OPTIONAL_TAG = re.compile(r'<br\s*/?>', re.I)


def required_tags(s: str) -> list[str]:
    return [t for t in TAG.findall(s) if not OPTIONAL_TAG.fullmatch(t)]
PLACEHOLDER = re.compile(r'%[-+ #0]*\d*(?:\.\d+)?[sdifxXc%]')
LETTER = re.compile(r'[A-Za-z]')


def is_source_line(line: str) -> bool:
    s = line.strip()
    if not s or s.startswith('//'):
        return False
    return bool(LETTER.search(PLACEHOLDER.sub('', TAG.sub('', s))))  # "- %s" or "<br>" alone is not text


def split_indent(line: str) -> tuple[str, str]:
    """Separates leading whitespace (re-applied by us, not trusted to the model)."""
    body = line.lstrip(' \t　')
    return line[:len(line) - len(body)], body


@dataclass
class Row:
    index: int
    en: str
    pt: str

    @property
    def lines(self) -> list[str]:
        return LINE_SEP.split(self.en)[0::2]

    @property
    def seps(self) -> list[str]:
        return LINE_SEP.split(self.en)[1::2]  # scene scripts use CRLF, _system.nut uses LF

    @property
    def source(self) -> list[str]:
        return [split_indent(l)[1] for l in self.lines if is_source_line(l)]

    @property
    def japanese(self) -> list[str]:
        return [l.strip()[2:].strip() for l in self.lines if l.strip().startswith('//') and l.strip()[2:].strip()]

    @property
    def translatable(self) -> bool:
        return bool(self.source)

    @property
    def done(self) -> bool:
        return bool(self.pt)

    def build_pt(self, translated: list[str]) -> str:
        """Rebuilds the block with the English lines swapped for the translation, in order."""
        it = iter(translated)
        out = []
        for line in self.lines:
            if is_source_line(line):
                indent, _ = split_indent(line)
                out.append(indent + next(it))
            else:
                out.append(line)
        return ''.join(l + s for l, s in zip(out, self.seps + ['']))


@dataclass
class Scene:
    path: Path
    rows: list[Row] = field(default_factory=list)
    raw: list[dict] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.stem

    @classmethod
    def load(cls, path: Path) -> 'Scene':
        raw = json.loads(path.read_text(encoding='utf-8'))
        rows = [Row(i, r['en'], r.get('pt', '')) for i, r in enumerate(raw)]
        return cls(path, rows, raw)

    @property
    def total(self) -> int:
        return sum(r.translatable for r in self.rows)

    @property
    def translated(self) -> int:
        return sum(r.translatable and r.done for r in self.rows)

    @property
    def pending(self) -> list[Row]:
        return [r for r in self.rows if r.translatable and not r.done]

    def set_pt(self, row: Row, pt: str) -> None:
        row.pt = pt
        self.raw[row.index]['pt'] = pt

    def save(self) -> None:
        # write, fsync, rename: an interrupted save never leaves a half-written scene
        atomic_write(self.path, json.dumps(self.raw, ensure_ascii=False, indent=1))


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_scenes(folder: Path) -> list[Scene]:
    return [Scene.load(p) for p in sorted(folder.glob('*.json'))]


# ---------------------------------------------------------------- prompts

SYSTEM_PROMPT = """You are a professional literary translator localizing the visual novel \
"Full Metal Daemon Muramasa" (Nitroplus) from English into Brazilian Portuguese (pt-BR).

Rules:
- Translate faithfully. Keep meaning, tone, register and each character's voice. Muramasa mixes \
formal, archaic, military and coarse speech: keep those differences.
- The Japanese original is given for each item as reference. Use it to resolve ambiguity and \
nuance; translate the English, do not re-translate the Japanese from scratch.
- Keep character names, honorifics (-san, -dono, -sama...) and in-world terms (tsurugi, musha...) \
as in the glossary. Never translate names.
- Keep every markup tag (<center>, <...>) and placeholder (%s, %d) exactly as they appear, in the same order.
- Exception: <br> is only a manual line break placed for English word wrapping. Drop it, or put it \
where a break reads naturally in Portuguese; never leave it in the middle of a word.
- Each item has a list of English lines. Return exactly the same number of lines, each line \
translating the line at the same position. Do not merge or split lines.
- Natural Brazilian Portuguese, correct accents, no explanations or notes.
"""


def system_prompt(glossary: str) -> str:
    g = glossary.strip()
    return SYSTEM_PROMPT + (f'\nGlossary (term = translation):\n{g}\n' if g else '')


def build_request(scene: Scene, rows: list[Row], context: list[tuple[str, str]]) -> str:
    payload = {
        'scene': scene.name,
        'previous_lines': [{'en': e, 'pt': p} for e, p in context],
        'items': [{'id': r.index, 'ja': r.japanese, 'en': r.source} for r in rows],
    }
    return ('Translate the items. previous_lines is already translated context, do not return it.\n'
            + json.dumps(payload, ensure_ascii=False))


RESPONSE_SCHEMA = {
    'type': 'object',
    'properties': {'items': {'type': 'array', 'items': {
        'type': 'object',
        'properties': {'id': {'type': 'integer'}, 'pt': {'type': 'array', 'items': {'type': 'string'}}},
        'required': ['id', 'pt'],
    }}},
    'required': ['items'],
}


def chunk_rows(rows: list[Row], max_items: int = 40, max_chars: int = 6000) -> list[list[Row]]:
    chunks, cur, size = [], [], 0
    for r in rows:
        n = sum(len(l) for l in r.source)
        if cur and (len(cur) >= max_items or size + n > max_chars):
            chunks.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += n
    if cur:
        chunks.append(cur)
    return chunks


class ValidationError(Exception):
    pass


def check_line(src: str, out: str) -> None:
    if not out.strip():
        raise ValidationError(f'empty translation for: {src[:60]}')
    if required_tags(src) != required_tags(out):
        raise ValidationError(f'tags changed: {src[:60]}')
    if sorted(PLACEHOLDER.findall(src)) != sorted(PLACEHOLDER.findall(out)):
        raise ValidationError(f'placeholders changed: {src[:60]}')
    if '\r' in out or '\n' in out:
        raise ValidationError(f'line break inside a line: {src[:60]}')


def parse_response(text: str, rows: list[Row]) -> dict[int, list[str]]:
    """Returns {row index: translated lines}. Rows missing or invalid are left out (retried later)."""
    try:
        items = json.loads(text)['items']
    except (ValueError, KeyError, TypeError) as e:
        raise ValidationError(f'invalid JSON from model: {e}') from e
    by_id = {r.index: r for r in rows}
    good, errors = {}, []
    for it in items:
        row = by_id.get(it.get('id')) if isinstance(it, dict) else None
        if row is None:
            continue
        pt = it.get('pt')
        try:
            if not isinstance(pt, list) or len(pt) != len(row.source):
                raise ValidationError(f'line count {len(pt) if isinstance(pt, list) else "?"} != {len(row.source)}')
            for s, o in zip(row.source, pt):
                check_line(s, o)
        except ValidationError as e:
            errors.append(f'#{row.index}: {e}')
            continue
        good[row.index] = pt
    if not good and errors:
        raise ValidationError('; '.join(errors[:3]))
    return good
