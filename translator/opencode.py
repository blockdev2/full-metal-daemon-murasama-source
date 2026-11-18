"""OpenCode provider: drives the local `opencode` CLI (`opencode run`) so free OpenCode models such as
opencode/deepseek-v4.1-flash-free are used through OpenCode itself, as its terms require.

Each request is written to a temp file and attached with --file (no command-line length limit) and the
CLI runs inside an empty private folder, so the agent never sees your project files.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

DEFAULT_MODEL = 'opencode/deepseek-v4.1-flash-free'
INSTRUCTION = ('Follow the instructions in the attached file. Do not use any tools. '
               'Reply with only the JSON object it asks for, no markdown fences, no comments.')
_RATE = re.compile(r'rate.?limit|too many requests|\b429\b|quota|overloaded|\b50[234]\b|unavailable', re.I)


def find_cli(configured: str = '') -> str:
    """The configured path, `opencode` on PATH, or the OpenCode desktop app's bundled CLI."""
    candidates = [configured, shutil.which('opencode') or '', shutil.which('opencode-cli') or '',
                  str(Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'ai.opencode.desktop' / 'resources'
                      / 'opencode-cli.exe')]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    raise FileNotFoundError('OpenCode CLI not found. Install OpenCode or set its path in Settings.')


def list_models(cli: str = '') -> list[str]:
    out = subprocess.run([find_cli(cli), 'models'], capture_output=True, text=True, encoding='utf-8',
                         errors='replace', timeout=120, creationflags=_NO_WINDOW)
    names = [l.strip() for l in out.stdout.splitlines() if re.fullmatch(r'[\w.\-]+/[\w.\-:]+', l.strip())]
    return sort_models(names)


def plan_of(model: str) -> str:
    """'go' = covered by the OpenCode Go subscription, 'free' = free model, 'paid' = pay-as-you-go credits."""
    if model.startswith('opencode-go/'):
        return 'go'
    return 'free' if model.endswith('-free') else 'paid'


def explain(model: str, problem: str) -> str:
    """Adds a plain hint to OpenCode billing errors, which are easy to hit by picking a look-alike model."""
    if 'provider.quota' in problem or 'Insufficient balance' in problem:
        go = 'opencode-go/' + model.split('/', 1)[-1].removesuffix('-free')
        return (f'{problem[:160]}  →  {model} is billed to pay-as-you-go credits. With the Go plan pick '
                f'a "· Go plan" model, e.g. {go}')
    if 'provider.free-limit' in problem:
        return f'{problem[:160]}  →  free quota used up; pick a "· Go plan" model if you have OpenCode Go'
    if 'outside Europe and the United States' in problem:
        return f'{problem[:160]}  →  enable Global in OpenCode console › Settings › Privacy › Regions'
    return problem


def sort_models(names: list[str]) -> list[str]:
    # Go subscription models first, then free ones, then pay-as-you-go
    return sorted(set(names), key=lambda n: ({'go': 0, 'free': 1, 'paid': 2}[plan_of(n)], n))


def extract_text(stdout: str) -> tuple[str, str]:
    """Joins the assistant text parts of `opencode run --format json`; also returns any error text."""
    text, errors = [], []
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        part = ev.get('part') or {}
        if ev.get('type') == 'text' and isinstance(part.get('text'), str):
            text.append(part['text'])
        elif ev.get('type') == 'error' or 'error' in ev:
            errors.append(json.dumps(ev.get('error', ev), ensure_ascii=False)[:300])
    return ''.join(text), ' '.join(errors)


def extract_json(text: str) -> str:
    """Models sometimes wrap the answer in ```json fences or add a sentence; keep the outer object."""
    t = text.strip()
    t = re.sub(r'^```(?:json)?\s*|\s*```$', '', t)
    a, b = t.find('{'), t.rfind('}')
    return t[a:b + 1] if a != -1 and b > a else t


_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class OpenCodeTranslator:
    def __init__(self, model: str, rpm: int, log: Callable[[str], None], cli: str = '', timeout: int = 300):
        self.cli = find_cli(cli)
        self.model = model or DEFAULT_MODEL
        self.interval = 60.0 / max(1, rpm)
        self.log = log
        self.timeout = timeout
        self.workdir = Path(tempfile.mkdtemp(prefix='muramasa_oc_'))  # empty: nothing for the agent to touch
        self._last = 0.0

    def _pace(self, stop: threading.Event) -> None:
        wait = self._last + self.interval - time.monotonic()
        if wait > 0:
            stop.wait(wait)
        self._last = time.monotonic()

    def _run(self, system: str, prompt: str) -> tuple[int, str, str]:
        req = self.workdir / 'request.md'
        req.write_text(f'{system}\n\n---\n\n{prompt}\n', encoding='utf-8')
        p = subprocess.run([self.cli, 'run', '-m', self.model, '--archived', '--format', 'json',
                            '--file', str(req), INSTRUCTION],
                           cwd=self.workdir, capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=self.timeout, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        return p.returncode, p.stdout, p.stderr

    def generate(self, system: str, prompt: str, stop: threading.Event) -> str | None:
        delay, attempt = 10.0, 0
        while not stop.is_set():
            self._pace(stop)
            attempt += 1
            try:
                code, out, err = self._run(system, prompt)
            except subprocess.TimeoutExpired:
                code, out, err = -1, '', f'no answer in {self.timeout}s'
            text, ev_err = extract_text(out)
            if code == 0 and text.strip():
                return extract_json(text)
            problem = (ev_err or err or f'exit code {code}, empty answer').strip()
            billing = any(k in problem for k in ('provider.quota', 'provider.free-limit', 'invalid-request'))
            if billing or (code != 0 and not _RATE.search(problem) and 'no answer' not in problem and attempt >= 3):
                raise RuntimeError(f'OpenCode failed: {explain(self.model, problem)[:400]}')
            self.log(f'! OpenCode: {problem[:120]} · try {attempt}, waiting {delay:.0f}s (p pauses)')
            if stop.wait(delay):
                return None
            delay = min(delay * 2, 120)
        return None

    def ping(self) -> str:
        code, out, err = self._run('You are a test.', 'Reply with exactly: ok')
        text, ev_err = extract_text(out)
        if code != 0 or not text.strip():
            raise RuntimeError(explain(self.model, (ev_err or err or f'exit code {code}').strip())[:400])
        return text.strip()
