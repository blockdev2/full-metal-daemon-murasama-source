"""Gemini client with request pacing and retries. Runs in a worker thread, never in the UI."""
from __future__ import annotations

import re
import threading
import time
from typing import Callable

from google import genai
from google.genai import errors, types

from .core import RESPONSE_SCHEMA

RETRYABLE = (429, 500, 502, 503, 504)


class GeminiTranslator:
    def __init__(self, api_key: str, model: str, rpm: int, log: Callable[[str], None],
                 on_model_change: Callable[[str], None] | None = None):
        # SDK retries disabled: our loop retries and logs every wait; 120 s cap per request
        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
            timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1)))
        self.model = model
        self.interval = 60.0 / max(1, rpm)
        self.log = log
        self.on_model_change = on_model_change
        self._last = 0.0

    def _pace(self, stop: threading.Event) -> None:
        wait = self._last + self.interval - time.monotonic()
        if wait > 0:
            stop.wait(wait)
        self._last = time.monotonic()

    def generate(self, system: str, prompt: str, stop: threading.Event) -> str | None:
        """Returns the JSON text, or None if stopped. Raises on non-retryable errors."""
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.3,
            response_mime_type='application/json',
            response_json_schema=RESPONSE_SCHEMA,
        )
        delay, attempt = 10.0, 0
        # overload/quota errors are waited out indefinitely (logged), until the user pauses
        while not stop.is_set():
            self._pace(stop)
            attempt += 1
            try:
                resp = self.client.models.generate_content(model=self.model, contents=prompt, config=cfg)
                if not resp.text:
                    raise ValueError(f'empty response (finish reason: {_finish(resp)})')
                return resp.text
            except errors.APIError as e:
                new = replacement_model(e) if e.code == 404 else None
                if new and new != self.model:
                    self.log(f'! model {self.model} retired by Google, switching to {new}')
                    self.model = new
                    if self.on_model_change:
                        self.on_model_change(new)
                    continue
                if e.code not in RETRYABLE:
                    raise
                wait = _retry_after(e) or delay
                why = {429: 'quota/rate limit', 503: 'Google servers overloaded', 504: 'Google timed out'}.get(
                    e.code, 'server error')
                self.log(f'! {e.code} {why}, try {attempt}, waiting {wait:.0f}s (p pauses)')
                if stop.wait(wait):
                    return None
                delay = min(delay * 2, 120)
        return None

    def ping(self) -> str:
        """One tiny real request: the model list also shows models a key can no longer call."""
        resp = self.client.models.generate_content(model=self.model, contents='Reply with: ok')
        return (resp.text or '').strip()

    def list_models(self) -> list[str]:
        return sorted(m.name.removeprefix('models/') for m in self.client.models.list()
                      if 'generateContent' in (m.supported_actions or []))


def _finish(resp) -> str:
    try:
        return str(resp.candidates[0].finish_reason)
    except (AttributeError, IndexError, TypeError):
        return 'unknown'


NOT_FOR_TEXT = ('image', 'embedding', 'tts', 'audio', 'live', 'research', 'aqa', 'veo', 'imagen', 'robotics')


def text_models(names: list[str]) -> list[str]:
    """Keeps Gemini models usable for text translation, newest first, flash before pro, lite last."""
    def ver(n: str) -> float:
        m = re.search(r'gemini-(\d+(?:\.\d+)?)', n)
        return float(m.group(1)) if m else 0.0
    keep = [n for n in names if n.startswith('gemini') and not any(x in n for x in NOT_FOR_TEXT)]
    return sorted(set(keep), key=lambda n: ('lite' in n, 'flash' not in n, -ver(n), n))


def replacement_model(e: Exception) -> str | None:
    """Google's 404 for retired models says 'use models/<name>'; returns that name."""
    m = re.search(r'use (?:models/)?(gemini-[\w.\-]*\w)', str(e))  # ends on a word char: "3.8" kept whole
    return m.group(1) if m else None


def _retry_after(e: errors.APIError) -> float | None:
    m = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s", str(e))
    return float(m.group(1)) + 1 if m else None
