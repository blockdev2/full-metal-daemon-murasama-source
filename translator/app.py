"""Textual interface: scene list, preview, translation queue and settings."""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from rich.markup import escape
from rich.table import Table
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Input, Label, ProgressBar, RichLog, Static

from .config import Config
from .core import Row, Scene, ValidationError, build_request, chunk_rows, load_scenes, parse_response, system_prompt
from .settings import SettingsScreen
from .state import JOURNAL_PATH, STATE_PATH, Checkpoint, Journal

MIN_W, MIN_H = 80, 24


def status_mark(s: Scene) -> Text:
    if s.total == 0:
        return Text('–', style='dim')
    if s.translated == s.total:
        return Text('✓', style='green')
    if s.translated:
        return Text('◐', style='yellow')
    return Text('·', style='dim')


class SessionScreen(ModalScreen[str | None]):
    """Shows the checkpoint and journal; returns the chosen action name."""
    BINDINGS = [Binding('escape', 'close', 'Close')]
    DANGER = {'discard': 'Discard session', 'clear_failed': 'Clear failed list', 'clear_journal': 'Delete journal'}

    def __init__(self, cp: Checkpoint, journal: Journal, busy: bool):
        super().__init__()
        self.cp, self.journal, self.busy = cp, journal, busy
        self.armed = ''

    def compose(self) -> ComposeResult:
        cp = self.cp
        if self.busy:
            status = Text('● running', style='green')
        elif cp.interrupted:
            status = Text('! interrupted', style='yellow')
        else:
            status = Text('– no unfinished session', style='dim')
        info = Table.grid(padding=(0, 2))
        info.add_column(style='dim', width=16)
        info.add_column()
        info.add_row('Status', status)
        info.add_row('Started', cp.started or '—')
        info.add_row('Last save', cp.updated or '—')
        info.add_row('Current scene', cp.current or '—')
        left = ', '.join(cp.queue[:8]) + (f'  … +{len(cp.queue) - 8}' if len(cp.queue) > 8 else '')
        info.add_row('Scenes left', f'{len(cp.queue)}' + (f'  ({left})' if cp.queue else ''))
        info.add_row('Done this session', f'{cp.strings_done:,} strings')
        failed = ', '.join(f'{k} ({len(v)})' for k, v in list(cp.failed.items())[:6])
        info.add_row('Failed strings', Text(f'{cp.failed_count}  {failed}', style='yellow' if cp.failed_count else ''))
        info.add_row('Journal', f'{self.journal.count():,} entries · {self.journal.size() / 1024:,.0f} KB'
                                f'  ({self.journal.path.name})')
        with Vertical(id='session'):
            yield Label('Session & recovery', classes='title')
            yield Static(info)
            yield Static(Text('Translations already saved in texto/ are never removed by these buttons.',
                              style='dim'), id='session_note')
            with Horizontal(id='buttons'):
                yield Button('Continue', id='resume', variant='primary', disabled=self.busy or not cp.interrupted)
                for key, label in self.DANGER.items():
                    yield Button(label, id=key, variant='warning', disabled=self.busy)
                yield Button('Close', id='close')

    @on(Button.Pressed)
    def pressed(self, e: Button.Pressed) -> None:
        bid = e.button.id
        if bid == 'close':
            self.dismiss(None)
        elif bid == 'resume':
            self.dismiss('resume')
        elif bid in self.DANGER:
            if self.armed == bid:  # second press confirms
                self.dismiss(bid)
                return
            self.armed = bid  # buttons keep their size: the confirmation shows in the note line
            self.query_one('#session_note', Static).update(
                Text(f'! Press "{self.DANGER[bid]}" again to confirm.', style='bold yellow'))

    def action_close(self) -> None:
        self.dismiss(None)


class TranslatorApp(App):
    TITLE = 'Muramasa Translator'
    CSS_PATH = 'app.tcss'
    BINDINGS = [
        Binding('t', 'translate_one', 'Translate scene'),
        Binding('a', 'translate_all', 'Translate all'),
        Binding('p', 'pause', 'Pause'),
        Binding('c', 'resume', 'Continue'),
        Binding('h', 'session', 'Session'),
        Binding('s', 'settings', 'Settings'),
        Binding('slash', 'filter', 'Filter'),
        Binding('r', 'reload', 'Reload', show=False),
        Binding('q', 'quit', 'Quit'),
    ]

    def __init__(self, config: Config | None = None, texts_dir: Path | None = None, client_factory=None,
                 state_dir: Path | None = None, model_lister=None):
        super().__init__()
        self.model_lister = model_lister
        self.config = config or Config.load()
        self.texts_dir = texts_dir or Path(self.config.texts_dir)
        base = state_dir or Path('.')
        self.journal = Journal(base / JOURNAL_PATH.name)
        self.state_path = base / STATE_PATH.name
        self.checkpoint = Checkpoint.load(self.state_path)
        self.scenes: list[Scene] = []
        self.stop = threading.Event()
        self.busy = False
        self.client_factory = client_factory  # tests inject a fake Gemini

    # ----------------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        with Horizontal(id='top'):
            yield Static(self.TITLE, id='title')
            yield Static('', id='model_info')
        with Horizontal(id='progress_row'):
            yield ProgressBar(id='progress', show_eta=False, show_percentage=True)
            yield Static('', id='progress_label')
        with Horizontal(id='main'):
            with Vertical(id='left'):
                yield Input(placeholder='/ filter scenes', id='filter')
                yield DataTable(id='scenes', cursor_type='row', zebra_stripes=False)
            with VerticalScroll(id='preview_box'):
                yield Static('', id='preview')
        yield RichLog(id='log', markup=True, wrap=True, max_lines=500)
        yield Static('', id='small')
        yield Footer()

    def on_mount(self) -> None:
        t = self.query_one('#scenes', DataTable)
        t.add_column('', key='st', width=1)
        t.add_column('scene', key='name', width=16)
        t.add_column('done', key='done', width=11)
        self.action_reload()
        self.refresh_header()
        if self.config.provider == 'gemini' and not self.config.effective_key:
            self.log_line('! No API key yet. Press [b]s[/b] to paste one, or pick the OpenCode provider.')
        cp = self.checkpoint
        if cp.interrupted:
            self.log_line(f'[yellow]! Unfinished session from {cp.updated}: {len(cp.queue)} scenes left '
                          f'(stopped at {cp.current or cp.queue[0]}). Press [b]c[/b] to continue, '
                          f'[b]h[/b] to see or discard it.[/yellow]')

    def on_resize(self, event: events.Resize) -> None:
        w, h = event.size
        small = w < MIN_W or h < MIN_H
        self.query_one('#small', Static).update(
            f'Terminal too small ({w}x{h}). Need at least {MIN_W}x{MIN_H}.'
            if small else '')
        self.query_one('#small').display = small
        for w in ('#top', '#progress_row', '#main', '#log'):
            self.query_one(w).display = not small

    # ----------------------------------------------------------- data/view
    def action_reload(self) -> None:
        if self.busy:
            self.log_line('! Pause the queue before reloading.')
            return
        if not self.texts_dir.is_dir():
            self.log_line(f'✗ Folder not found: {escape(str(self.texts_dir))}. Run extract_text.py first.')
            return
        self.scenes = load_scenes(self.texts_dir)
        restored = self.journal.replay(self.scenes)
        if restored:
            self.log_line(f'[green]✓ Recovered {restored} translations from the journal that had not reached '
                          f'their scene files (previous run was interrupted).[/green]')
        self.fill_table()
        self.refresh_header()
        self.log_line(f'Loaded {len(self.scenes)} scenes from {escape(str(self.texts_dir))}')

    def visible_scenes(self) -> list[Scene]:
        q = self.query_one('#filter', Input).value.strip().lower()
        return [s for s in self.scenes if q in s.name.lower()]

    def fill_table(self) -> None:
        t = self.query_one('#scenes', DataTable)
        t.clear()
        for s in self.visible_scenes():
            t.add_row(status_mark(s), s.name, f'{s.translated}/{s.total}', key=s.name)
        self.show_preview(self.current_scene())

    def update_row(self, s: Scene) -> None:
        t = self.query_one('#scenes', DataTable)
        if s.name in t.rows:
            t.update_cell(s.name, 'st', status_mark(s))
            t.update_cell(s.name, 'done', f'{s.translated}/{s.total}')

    def refresh_header(self) -> None:
        total = sum(s.total for s in self.scenes)
        done = sum(s.translated for s in self.scenes)
        bar = self.query_one('#progress', ProgressBar)
        bar.update(total=max(total, 1), progress=done)
        self.query_one('#progress_label', Static).update(f'{done:,} / {total:,} strings')
        if self.busy:
            state = 'pausing…' if self.stop.is_set() else 'translating'
        else:
            state = 'interrupted, c to continue' if self.checkpoint.interrupted else 'idle'
        self.query_one('#model_info', Static).update(
            f'{self.config.active_model} · {self.config.requests_per_minute} req/min · {self.config.key_label} · {state}')

    def current_scene(self) -> Scene | None:
        t = self.query_one('#scenes', DataTable)
        if not t.row_count:
            return None
        key = t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value
        return next((s for s in self.scenes if s.name == key), None)

    def show_preview(self, s: Scene | None) -> None:
        box = self.query_one('#preview', Static)
        if s is None:
            box.update(Text('No scenes match the filter.', style='dim'))
            return
        tab = Table.grid(padding=(0, 1))
        tab.add_column(width=2, style='dim')
        tab.add_column(ratio=1)
        for r in s.rows:
            if not r.translatable:
                continue
            tab.add_row('EN', Text('\n'.join(r.source), style='dim'))
            pt = Row(r.index, r.pt, '').source if r.pt else []
            tab.add_row('PT', Text('\n'.join(pt)) if pt else Text('—', style='dim'))
            tab.add_row('', '')
        head = Text.assemble((s.name, 'bold'), (f'  ·  {s.translated} / {s.total} strings\n', 'dim'))
        box.update(_stack(head, tab))

    def preview_if_current(self, s: Scene) -> None:
        if self.current_scene() is s:
            self.show_preview(s)

    @on(DataTable.RowHighlighted, '#scenes')
    def _highlight(self) -> None:
        self.show_preview(self.current_scene())

    @on(Input.Changed, '#filter')
    def _filter(self) -> None:
        self.fill_table()

    @on(Input.Submitted, '#filter')
    def _filter_done(self) -> None:
        self.query_one('#scenes', DataTable).focus()

    def action_filter(self) -> None:
        self.query_one('#filter', Input).focus()

    def log_line(self, msg: str) -> None:
        self.query_one('#log', RichLog).write(f'[dim]{datetime.now():%H:%M:%S}[/dim]  {msg}')

    # ----------------------------------------------------------- settings
    def action_settings(self) -> None:
        def done(cfg: Config | None) -> None:
            if cfg is None:
                return
            self.config = cfg
            cfg.save()
            self.log_line('✓ Settings saved')
            self.refresh_header()
        self.push_screen(SettingsScreen(self.config, self.model_lister), done)

    # ----------------------------------------------------------- session panel
    def action_session(self) -> None:
        self.push_screen(SessionScreen(self.checkpoint, self.journal, self.busy), self.session_action)

    def session_action(self, action: str | None) -> None:
        if action is None:
            return
        if self.busy:
            self.log_line('! Pause the queue first.')
            return
        cp = self.checkpoint
        if action == 'resume':
            self.action_resume()
        elif action == 'discard':
            failed = cp.failed
            self.checkpoint = cp = Checkpoint(failed=failed)  # keep the failed list, drop the queue
            cp.save(self.state_path)
            self.log_line('Session discarded. Translations already saved were kept.')
        elif action == 'clear_failed':
            cp.failed = {}
            cp.save(self.state_path)
            self.log_line('Failed list cleared.')
        elif action == 'clear_journal':
            self.journal.clear()
            self.log_line('Journal deleted. Scene files are untouched.')
        self.refresh_header()

    # ----------------------------------------------------------- queue
    def action_translate_one(self) -> None:
        s = self.current_scene()
        if s:
            self.start([s])

    def action_translate_all(self) -> None:
        self.start([s for s in self.visible_scenes() if s.pending])

    def action_pause(self) -> None:
        if self.busy:
            self.stop.set()
            self.log_line('Pausing after the current request…')
            self.refresh_header()

    def action_resume(self) -> None:
        cp = self.checkpoint
        if not cp.interrupted:
            self.log_line('No unfinished session to continue.')
            return
        by_name = {s.name: s for s in self.scenes}
        self.start([by_name[n] for n in cp.queue if n in by_name], resume=True)

    def start(self, scenes: list[Scene], resume: bool = False) -> None:
        if self.busy:
            self.log_line('! Already translating. Press [b]p[/b] to pause first.')
            return
        scenes = [s for s in scenes if s.pending]
        if not scenes:
            if resume:
                self.checkpoint.queue = []
                self.checkpoint.save(self.state_path)
            self.log_line('Nothing left to translate here.')
            return
        if self.config.provider == 'gemini' and not self.config.effective_key and not self.client_factory:
            self.log_line('✗ No API key. Press [b]s[/b] to add one, or switch the provider to OpenCode.')
            return
        if self.checkpoint.interrupted and not resume:
            self.log_line(f'[yellow]! Replacing the unfinished session ({len(self.checkpoint.queue)} scenes). '
                          f'Already translated strings are kept.[/yellow]')
        if resume:
            self.checkpoint.queue = [s.name for s in scenes]
        else:
            self.checkpoint.begin([s.name for s in scenes])
        self.checkpoint.save(self.state_path)
        self.stop.clear()
        self.busy = True
        self.refresh_header()
        n = sum(len(s.pending) for s in scenes)
        self.log_line(f'{"Continuing" if resume else "Queue"}: {len(scenes)} scenes, {n:,} strings')
        self.run_queue(scenes)

    @work(thread=True, exclusive=True, group='queue')
    def run_queue(self, scenes: list[Scene]) -> None:
        ui = self.call_from_thread
        try:
            client = self.client_factory() if self.client_factory else self._client(ui)
            glossary = _read(Path(self.config.glossary_path))
            system = system_prompt(glossary)
            for s in scenes:
                if self.stop.is_set():
                    break
                if self.translate_scene(client, system, s, ui):
                    self.checkpoint.finish_scene(s.name)
                    self.checkpoint.save(self.state_path)
        except Exception as e:  # keep the app alive and tell the user
            ui(self.log_line, f'[red]✗ {escape(str(e))[:400]}[/red]')
        finally:
            self.checkpoint.save(self.state_path)
            self.busy = False
            ui(self.refresh_header)
            cp = self.checkpoint
            if cp.interrupted:
                ui(self.log_line, f'Stopped. {len(cp.queue)} scenes left in the session, press [b]c[/b] to continue.')
            else:
                ui(self.log_line, f'Session finished: {cp.strings_done:,} strings translated'
                                  + (f', {cp.failed_count} failed (see [b]h[/b])' if cp.failed_count else '') + '.')

    def _client(self, ui):
        c = self.config
        warn = lambda m: ui(self.log_line, f'[yellow]{escape(m)}[/yellow]')
        if c.provider == 'opencode':
            from .opencode import OpenCodeTranslator
            return OpenCodeTranslator(c.opencode_model, c.requests_per_minute, warn, c.opencode_cli)
        from .gemini import GeminiTranslator
        return GeminiTranslator(c.effective_key, c.model, c.requests_per_minute, warn,
                                lambda m: ui(self.model_changed, m))

    def model_changed(self, model: str) -> None:
        self.config.model = model
        self.config.save()
        self.refresh_header()

    def translate_scene(self, client, system: str, s: Scene, ui) -> bool:
        """Returns True when the scene was fully processed (not interrupted by pause/stop)."""
        done_before = s.translated
        self.checkpoint.current = s.name
        self.checkpoint.save(self.state_path)
        for chunk in chunk_rows(s.pending):
            # context: the last translated lines before this chunk, for continuity
            prev = [r for r in s.rows if r.done and r.translatable and r.index < chunk[0].index][-6:]
            context = [(' / '.join(r.source), ' / '.join(Row(0, r.pt, '').source)) for r in prev]
            for attempt in range(3):
                if self.stop.is_set():
                    return False
                text = client.generate(system, build_request(s, chunk, context), self.stop)
                if text is None:
                    return False
                try:
                    good = parse_response(text, chunk)
                except ValidationError as e:
                    ui(self.log_line, f'[yellow]! {s.name}: {escape(str(e))[:200]} (attempt {attempt + 1}/3)[/yellow]')
                    continue
                built = [(r, r.build_pt(good[r.index])) for r in chunk if r.index in good]
                # journal first (fsynced), then the scene file, then the checkpoint
                self.journal.append(s.name, [(r.index, r.en, pt) for r, pt in built])
                for r, pt in built:
                    s.set_pt(r, pt)
                s.save()
                self.checkpoint.strings_done += len(built)
                self.checkpoint.mark_done(s.name, [r.index for r, _ in built])
                self.checkpoint.save(self.state_path)
                ui(self.update_row, s)
                ui(self.refresh_header)
                ui(self.preview_if_current, s)
                chunk = [r for r in chunk if not r.done]
                if not chunk:
                    break
            if chunk:
                self.checkpoint.mark_failed(s.name, [r.index for r in chunk])
                self.checkpoint.save(self.state_path)
                ui(self.log_line, f'[yellow]! {s.name}: {len(chunk)} strings skipped (listed in [b]h[/b], '
                                  f'retried next run)[/yellow]')
        mark = '✓' if s.translated == s.total else '◐'
        ui(self.log_line, f'{mark} {s.name}  +{s.translated - done_before} strings')
        return True


def _stack(head: Text, body: Table) -> Table:
    g = Table.grid()
    g.add_row(head)
    g.add_row(body)
    return g


def _read(p: Path) -> str:
    if not p.exists():
        return ''
    return '\n'.join(l for l in p.read_text(encoding='utf-8').splitlines() if l.strip() and not l.startswith('#'))
