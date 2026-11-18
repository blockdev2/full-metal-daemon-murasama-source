"""Interactive Gemini translator for the extracted script text (texto/*.json).

    python translate.py            open the interface
    python translate.py --status   print progress and exit (no interface)
"""
import sys
from pathlib import Path

from translator.config import Config
from translator.core import load_scenes


def main() -> int:
    if '--help' in sys.argv or '-h' in sys.argv:
        print(__doc__)
        return 0
    cfg = Config.load()
    if '--status' in sys.argv or not sys.stdout.isatty():
        scenes = load_scenes(Path(cfg.texts_dir))
        total, done = sum(s.total for s in scenes), sum(s.translated for s in scenes)
        print(f'{done}/{total} strings translated in {len(scenes)} scenes')
        return 0
    from translator.app import TranslatorApp
    TranslatorApp(cfg).run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
