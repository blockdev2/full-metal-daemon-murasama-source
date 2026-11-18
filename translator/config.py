"""Settings stored in translator_config.json (git-ignored: it may hold the API key)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_PATH = Path('translator_config.json')


@dataclass
class Config:
    provider: str = 'gemini'  # 'gemini' (Google API key) or 'opencode' (local OpenCode CLI)
    api_key: str = ''
    model: str = 'gemini-3.8-flash'
    opencode_model: str = 'opencode/deepseek-v4.1-flash-free'
    opencode_cli: str = ''  # empty: auto-detect
    requests_per_minute: int = 10
    texts_dir: str = 'texto'
    glossary_path: str = 'glossary.txt'

    @property
    def effective_key(self) -> str:
        return self.api_key or os.environ.get('GEMINI_API_KEY', '')

    @property
    def active_model(self) -> str:
        return self.opencode_model if self.provider == 'opencode' else self.model

    @property
    def key_label(self) -> str:
        if self.provider == 'opencode':
            return 'via OpenCode'
        if self.api_key:
            return 'saved key ' + '•' * 4 + self.api_key[-4:]
        if os.environ.get('GEMINI_API_KEY'):
            return 'GEMINI_API_KEY env var'
        return 'no API key'

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> 'Config':
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding='utf-8'))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding='utf-8')
