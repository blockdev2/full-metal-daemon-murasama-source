"""Monta uma cópia jogável do jogo a partir de jogo_extraido/.

Uso: python rebuild.py <pasta_do_jogo_original> <pasta_extraida> <pasta_saida>
Copia exe/dlls e regera cada .npk com os arquivos extraídos (patches oficiais já mesclados,
por isso a pasta patch/ sai vazia).
"""
import shutil, sys
from pathlib import Path
import npk


def main(game, extracted, out):
    game, extracted, out = Path(game), Path(extracted), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'patch').mkdir(exist_ok=True)
    for f in game.iterdir():
        if f.is_file() and f.suffix.lower() in ('.exe', '.dll') and not f.name.startswith('unins'):
            shutil.copy2(f, out / f.name)
    for arc in sorted(game.glob('*.npk')):
        with open(arc, 'rb') as f:
            _, entries = npk.read_index(f)
        files = {e[0]: (extracted / e[0]).read_bytes() for e in entries}
        npk.pack(files, out / arc.name, level=3, flags={e[0]: e[1] for e in entries})  # nível baixo: ~4 GB, a taxa de compressão não importa aqui
        print(f'{arc.name}: {len(files)} arquivos', flush=True)


if __name__ == '__main__':
    main(*sys.argv[1:4])
