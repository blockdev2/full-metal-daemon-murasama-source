"""Settings dialog: provider (Gemini or OpenCode), credentials, model list, rate and glossary."""
from __future__ import annotations

import time

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static
from textual.worker import get_current_worker

from .config import Config


def list_models(provider: str, key: str, cli: str) -> list[str]:
    if provider == 'opencode':
        from .opencode import list_models as oc_models
        return oc_models(cli)
    from .gemini import GeminiTranslator, text_models
    return text_models(GeminiTranslator(key, '', 60, lambda m: None).list_models())


class SettingsScreen(ModalScreen[Config | None]):
    BINDINGS = [Binding('escape', 'cancel', 'Cancel')]
    PROVIDERS = [('Google Gemini (API key)', 'gemini'), ('OpenCode (local CLI, free models)', 'opencode')]

    def __init__(self, config: Config, model_lister=None):
        super().__init__()
        self.config = config
        self.model_lister = model_lister or list_models  # (provider, key, cli) -> names; tests inject a fake

    def compose(self) -> ComposeResult:
        c = self.config
        with Vertical(id='settings'):
            yield Label('Settings', classes='title')
            yield Label('Provider', classes='field')
            yield Select(self.PROVIDERS, value=c.provider, allow_blank=False, id='provider')
            with Vertical(id='gemini_box', classes='group'):
                yield Label('Gemini API key  ·  aistudio.google.com/apikey', classes='field')
                yield Input(c.api_key, password=True,
                            placeholder='paste your key (or leave empty to use GEMINI_API_KEY)', id='api_key')
            with Vertical(id='opencode_box', classes='group'):
                yield Label('OpenCode CLI path (empty = auto-detect)', classes='field')
                yield Input(c.opencode_cli, placeholder='auto', id='opencode_cli')
            with Horizontal(classes='field_row'):
                yield Label('Model', classes='field')
                yield Static('', id='model_status')
            m = c.active_model
            yield Select([(m, m)], value=m, allow_blank=False, id='model')
            yield Label('Requests per minute (keep under your free-tier limit)', classes='field')
            yield Input(str(c.requests_per_minute), type='integer', id='rpm')
            yield Label('Glossary file', classes='field')
            yield Input(c.glossary_path, id='glossary')
            yield Static('', id='test_result')
            with Horizontal(id='buttons'):
                yield Button('Test', id='test')
                yield Button('Cancel', id='cancel')
                yield Button('Save', id='save', variant='primary')

    @property
    def provider(self) -> str:
        v = self.query_one('#provider', Select).value
        return v if isinstance(v, str) else 'gemini'

    def saved_model(self, provider: str) -> str:
        return self.config.opencode_model if provider == 'opencode' else self.config.model

    # ---- per-provider fields and model list
    def on_mount(self) -> None:
        self._show_provider_fields()
        self.load_models()

    @on(Select.Changed, '#provider')
    def _provider_changed(self) -> None:
        self._show_provider_fields()
        m = self.saved_model(self.provider)
        sel = self.query_one('#model', Select)
        sel.set_options([(m, m)])
        sel.value = m
        self.load_models()

    def _show_provider_fields(self) -> None:
        self.query_one('#gemini_box').display = self.provider == 'gemini'
        self.query_one('#opencode_box').display = self.provider == 'opencode'

    @on(Input.Changed, '#api_key')
    def _key_changed(self) -> None:
        if self.provider == 'gemini':
            self.load_models()

    @on(Input.Submitted, '#opencode_cli')
    def _cli_changed(self) -> None:
        self.load_models()

    def load_models(self) -> None:
        key = self.query_one('#api_key', Input).value.strip() or Config(api_key='').effective_key
        if self.provider == 'gemini' and not key:
            self.query_one('#model_status', Static).update(Text('  paste a key to list models', style='dim'))
            return
        self.query_one('#model_status', Static).update(Text('  … loading models', style='dim'))
        self._fetch_models(self.provider, key, self.query_one('#opencode_cli', Input).value.strip())

    @work(thread=True, exclusive=True, group='models')
    def _fetch_models(self, provider: str, key: str, cli: str) -> None:
        time.sleep(0.4)  # debounce typing/pasting; exclusive=True cancels the previous fetch
        if get_current_worker().is_cancelled:
            return
        try:
            models = self.model_lister(provider, key, cli)
        except Exception as e:  # bad key, CLI missing, offline...: keep the current choice, show why
            self.app.call_from_thread(self.query_one('#model_status', Static).update,
                                      Text(f'  ✗ {str(e)[:70]}', style='red'))
            return
        if not get_current_worker().is_cancelled:
            self.app.call_from_thread(self._set_models, provider, models)

    def _set_models(self, provider: str, models: list[str]) -> None:
        if provider != self.provider:
            return  # list arrived for a provider the user already switched away from
        sel = self.query_one('#model', Select)
        saved = self.saved_model(provider)
        current = sel.value if isinstance(sel.value, str) else saved
        options = list(models) or [current]
        if current not in options:
            options.insert(0, current)  # keep a saved model visible even if the list lacks it
        from .opencode import plan_of
        tag = {'go': '  · Go plan', 'free': '  · free', 'paid': '  · pay-as-you-go credits'}
        label = lambda m: (m + (tag[plan_of(m)] if provider == 'opencode' else '')
                           + ('  (current)' if m == saved else ''))
        sel.set_options([(label(m), m) for m in options])
        sel.value = current
        self.query_one('#model_status', Static).update(
            Text(f'  ✓ {len(models)} models  ·  click to choose', style='green'))

    def _collect(self) -> Config:
        v = lambda i: self.query_one(f'#{i}', Input).value.strip()
        rpm = int(v('rpm') or 10)
        model = self.query_one('#model', Select).value
        model = model if isinstance(model, str) else self.saved_model(self.provider)
        c = self.config
        oc = self.provider == 'opencode'
        return Config(provider=self.provider, api_key=v('api_key'),
                      model=c.model if oc else model, opencode_model=model if oc else c.opencode_model,
                      opencode_cli=v('opencode_cli'), requests_per_minute=max(1, rpm), texts_dir=c.texts_dir,
                      glossary_path=v('glossary') or 'glossary.txt')

    @on(Button.Pressed, '#save')
    def save(self) -> None:
        self.dismiss(self._collect())

    @on(Button.Pressed, '#cancel')
    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, '#test')
    def test(self) -> None:
        cfg = self._collect()
        who = 'OpenCode' if cfg.provider == 'opencode' else 'Gemini'
        self.query_one('#test_result', Static).update(Text(f'… asking {who} ({cfg.active_model})', style='dim'))
        self._test(cfg)

    @work(thread=True, exclusive=True)
    def _test(self, cfg: Config) -> None:
        try:
            if cfg.provider == 'opencode':
                from .opencode import OpenCodeTranslator
                OpenCodeTranslator(cfg.opencode_model, 60, lambda m: None, cfg.opencode_cli).ping()
                msg = Text(f'✓ OpenCode works  ·  {cfg.opencode_model} answered', style='green')
            else:
                msg = self._test_gemini(cfg)
        except Exception as e:  # shown to the user, never crashes the app
            msg = Text(f'✗ {e}'[:300], style='red')
        self.app.call_from_thread(self.query_one('#test_result', Static).update, msg)

    def _test_gemini(self, cfg: Config) -> Text:
        from .gemini import GeminiTranslator, replacement_model
        if not cfg.effective_key:
            raise ValueError('no API key')
        g = GeminiTranslator(cfg.effective_key, cfg.model, 60, lambda m: None)
        try:
            g.ping()
            return Text(f'✓ key works  ·  {cfg.model} answered', style='green')
        except Exception as e:
            new = replacement_model(e)
            if not new:
                raise
            g.model = new
            g.ping()
            self.app.call_from_thread(self._set_models, 'gemini', [new])
            return Text(f'✓ key works  ·  {cfg.model} is retired, switched to {new} (press Save)', style='green')
