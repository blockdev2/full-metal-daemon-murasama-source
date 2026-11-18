import json
import shutil
from pathlib import Path

import pytest

from translator.app import TranslatorApp
from translator.config import Config
from translator.core import Row, Scene, ValidationError, chunk_rows, parse_response
from translator.state import Checkpoint, Journal

ROOT = Path(__file__).resolve().parent.parent
BLOCK = '\r\n//　彼は破壊を求めた。\r\n  He never wished for destruction.\r\n\r\n<center>"I remember the sky."\r\n '


def test_row_source_and_rebuild():
    r = Row(0, BLOCK, '')
    assert r.source == ['He never wished for destruction.', '<center>"I remember the sky."']
    assert r.japanese == ['彼は破壊を求めた。']
    pt = r.build_pt(['Ele nunca desejou a destruição.', '<center>"Eu me lembro do céu."'])
    # Japanese comment, indentation, blank lines and trailing space all preserved
    assert pt == '\r\n//　彼は破壊を求めた。\r\n  Ele nunca desejou a destruição.\r\n\r\n<center>"Eu me lembro do céu."\r\n '


def test_lf_blocks_keep_their_separators():
    r = Row(0, ' \n<center>——- SKIPPED ——-</center>\n ', '')
    assert r.source == ['<center>——- SKIPPED ——-</center>']
    assert r.build_pt(['<center>——- PULADO ——-</center>']) == ' \n<center>——- PULADO ——-</center>\n '


def test_rows_without_text_are_skipped():
    assert not Row(0, ' ', '').translatable
    assert not Row(0, '- %s', '').translatable  # format string only, nothing to translate
    assert Row(0, '[%s] says hi', '').translatable
    assert not Row(0, '\r\n//　コメント\r\n ', '').translatable


def test_parse_response_validates_lines_tags_placeholders():
    rows = [Row(1, BLOCK, ''), Row(2, '[%s] says hi', '')]
    good = parse_response(json.dumps({'items': [
        {'id': 1, 'pt': ['Ele nunca desejou a destruição.', '"Eu me lembro do céu."']},  # tag dropped
        {'id': 2, 'pt': ['[%s] diz oi']},
    ]}), rows)
    assert good == {2: ['[%s] diz oi']}
    with pytest.raises(ValidationError):
        parse_response(json.dumps({'items': [{'id': 2, 'pt': ['diz oi']}]}), rows)  # placeholder lost
    with pytest.raises(ValidationError):
        parse_response('not json', rows)


def test_chunking_limits():
    rows = [Row(i, 'x' * 1000, '') for i in range(20)]
    chunks = chunk_rows(rows, max_items=40, max_chars=6000)
    assert [len(c) for c in chunks] == [6, 6, 6, 2]


class FakeGemini:
    """Translates by prefixing 'PT ' (keeps tags/placeholders), like a well-behaved model."""
    calls = 0

    def generate(self, system, prompt, stop):
        FakeGemini.calls += 1
        items = json.loads(prompt.split('\n', 1)[1])['items']
        return json.dumps({'items': [{'id': it['id'], 'pt': ['PT ' + l for l in it['en']]} for it in items]})


@pytest.fixture
def texts(tmp_path):
    d = tmp_path / 'texto'
    d.mkdir()
    for name in ('ma01_001', '_system'):
        shutil.copy(ROOT / 'texto' / f'{name}.json', d)
    return d


@pytest.mark.asyncio
async def test_tui_translate_scene_end_to_end(texts, tmp_path):
    cfg = Config(glossary_path=str(tmp_path / 'none.txt'))
    app = TranslatorApp(cfg, texts, client_factory=FakeGemini, state_dir=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        assert [s.name for s in app.scenes] == ['_system', 'ma01_001']
        await pilot.press('slash', *'ma01', 'enter')  # filter, then back to the table
        assert [s.name for s in app.visible_scenes()] == ['ma01_001']
        await pilot.press('t')
        await app.workers.wait_for_complete()
        await pilot.pause()
        s = app.scenes[1]
        assert s.translated == s.total > 0
        assert not app.busy
    saved = Scene.load(texts / 'ma01_001.json')
    r = next(r for r in saved.rows if r.translatable)
    assert r.pt.startswith('\r\n//') and 'PT He never wished for destruction.' in r.pt


@pytest.mark.asyncio
async def test_tui_small_terminal_and_settings(texts, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # settings are saved to translator_config.json in cwd
    app = TranslatorApp(Config(), texts, client_factory=FakeGemini, state_dir=tmp_path,
                        model_lister=lambda provider, key, cli: ['gemini-9-flash', 'gemini-9-pro'])
    async with app.run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        assert app.query_one('#small').display
        await pilot.resize_terminal(120, 40)
        await pilot.pause()
        assert not app.query_one('#small').display
        await pilot.press('s')
        await pilot.pause()
        app.screen.query_one('#api_key').value = 'test-key-1234'
        await pilot.pause(0.8)             # model list is fetched with the typed key (debounced)
        sel = app.screen.query_one('#model')
        assert [o[1] for o in sel._options][-2:] == ['gemini-9-flash', 'gemini-9-pro']
        sel.value = 'gemini-9-pro'
        await pilot.click('#save')
        await pilot.pause()
        assert app.config.api_key == 'test-key-1234'
        assert app.config.model == 'gemini-9-pro'
    assert json.loads((tmp_path / 'translator_config.json').read_text())['api_key'] == 'test-key-1234'


def test_journal_replay_restores_lost_saves(texts, tmp_path):
    scene = Scene.load(texts / 'ma01_001.json')
    row = next(r for r in scene.rows if r.translatable)
    j = Journal(tmp_path / 'j.jsonl')
    j.append('ma01_001', [(row.index, row.en, 'PT salvo'), (row.index, 'other english', 'WRONG')])
    with open(j.path, 'a', encoding='utf-8') as f:
        f.write('{"scene": "ma01_001", "ind')  # line cut by a crash mid-write
    assert j.replay([scene]) == 1          # entry with mismatching en is never applied
    assert Scene.load(texts / 'ma01_001.json').rows[row.index].pt == 'PT salvo'
    assert j.replay([Scene.load(texts / 'ma01_001.json')]) == 0  # idempotent


def test_checkpoint_roundtrip_and_failed(tmp_path):
    cp = Checkpoint()
    cp.begin(['a', 'b'])
    cp.mark_failed('a', [3, 5])
    cp.mark_done('a', [3])
    cp.finish_scene('a')
    cp.save(tmp_path / 's.json')
    back = Checkpoint.load(tmp_path / 's.json')
    assert back.queue == ['b'] and back.failed == {'a': [5]} and back.interrupted
    assert Checkpoint.load(tmp_path / 'missing.json').queue == []
    (tmp_path / 'bad.json').write_text('{broken')
    assert Checkpoint.load(tmp_path / 'bad.json').queue == []  # corrupt state never crashes the app


@pytest.mark.asyncio
async def test_tui_resumes_interrupted_session(texts, tmp_path):
    cp = Checkpoint()
    cp.begin(['ma01_001'])
    cp.current = 'ma01_001'
    cp.save(tmp_path / 'translator_state.json')  # as if the app died mid-scene
    app = TranslatorApp(Config(glossary_path=str(tmp_path / 'none.txt')), texts, client_factory=FakeGemini,
                        state_dir=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.checkpoint.interrupted
        await pilot.press('c')
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not app.checkpoint.interrupted
        s = next(s for s in app.scenes if s.name == 'ma01_001')
        assert s.translated == s.total
    assert Journal(tmp_path / 'translator_journal.jsonl').count() == s.total
    assert Checkpoint.load(tmp_path / 'translator_state.json').strings_done == s.total


@pytest.mark.asyncio
async def test_session_panel_delete_needs_confirmation(texts, tmp_path):
    j = Journal(tmp_path / 'translator_journal.jsonl')
    j.append('x', [(0, 'en', 'pt')])
    app = TranslatorApp(Config(), texts, client_factory=FakeGemini, state_dir=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press('h')
        await pilot.pause()
        await pilot.click('#clear_journal')
        await pilot.pause()
        assert j.path.exists()             # first press only arms the button
        await pilot.pause(0.5)             # Button ignores repeat presses during its press animation
        await pilot.click('#clear_journal')
        await pilot.pause()
        assert not j.path.exists()


def test_replacement_model_parsed_from_google_404():
    from translator.gemini import replacement_model
    msg = ("404 NOT_FOUND. {'error': {'code': 404, 'message': 'This model models/gemini-2.5-flash is no longer "
           "available to new users. Please update your code to use models/gemini-3.8-flash for the latest features "
           "and improvements.', 'status': 'NOT_FOUND'}}")
    assert replacement_model(Exception(msg)) == 'gemini-3.8-flash'
    assert replacement_model(Exception('404 model not found')) is None


def test_text_models_filter_and_order():
    from translator.gemini import text_models
    names = ['gemini-2.5-flash', 'gemini-3.1-flash-image', 'gemini-3.8-flash', 'gemini-3.5-flash-lite',
             'gemini-2.5-pro', 'deep-research-pro-preview-12-2025', 'gemini-embedding-001', 'gemini-3.6-flash']
    assert text_models(names) == ['gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-2.5-flash', 'gemini-2.5-pro',
                                  'gemini-3.5-flash-lite']


def test_opencode_output_parsing():
    from translator.opencode import extract_json, extract_text
    out = '\n'.join([
        '{"type":"step_start","part":{"type":"step-start"}}',
        '{"type":"text","part":{"type":"text","text":"```json\\n{\\"items\\": [{\\"id\\": 1, "}}',
        '{"type":"text","part":{"type":"text","text":"\\"pt\\": [\\"Ola\\"]}]}\\n```"}}',
        'not json at all',
        '{"type":"step_finish","part":{"type":"step-finish","cost":0}}',
    ])
    text, err = extract_text(out)
    assert err == ''
    assert json.loads(extract_json(text)) == {'items': [{'id': 1, 'pt': ['Ola']}]}
    assert extract_json('Here you go: {"items": []} hope it helps') == '{"items": []}'


@pytest.mark.asyncio
async def test_settings_switch_to_opencode(texts, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lister = lambda provider, key, cli: (['opencode/deepseek-v4.1-flash-free', 'opencode/glm-5.3']
                                         if provider == 'opencode' else ['gemini-9-flash'])
    app = TranslatorApp(Config(api_key='k'), texts, client_factory=FakeGemini, state_dir=tmp_path,
                        model_lister=lister)
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.press('s')
        await pilot.pause(0.8)
        scr = app.screen
        scr.query_one('#provider').value = 'opencode'
        await pilot.pause(0.8)
        assert not scr.query_one('#gemini_box').display and scr.query_one('#opencode_box').display
        assert 'opencode/glm-5.3' in [o[1] for o in scr.query_one('#model')._options]
        scr.query_one('#model').value = 'opencode/glm-5.3'
        await pilot.click('#save')
        await pilot.pause()
    assert app.config.provider == 'opencode' and app.config.opencode_model == 'opencode/glm-5.3'
    assert app.config.model == 'gemini-3.8-flash' and app.config.api_key == 'k'  # gemini choices kept


def test_br_is_optional_but_other_tags_are_not():
    from translator.core import check_line
    check_line("The dragoon's draw-and-<br>slash splits him", 'O golpe de saque do dragão o parte')   # br dropped
    check_line('A <br>B', 'Um B<br>')                                                               # br moved
    with pytest.raises(ValidationError):
        check_line('<center>"I remember."', '"Eu me lembro."')                                     # center lost
