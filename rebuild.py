"""Monta uma cópia jogável do jogo a partir de jogo_extraido/.

Uso: python rebuild.py <pasta_do_jogo_original> <pasta_extraida> <pasta_saida>
Copia exe/dlls, regera cada .npk com os arquivos extraídos e copia os patches oficiais (patch/*.npk):
eles trazem arquivos que não existem em nenhum .npk principal (ex.: media/cg/sys/title/photo.png).
Coloque os seus patches de tradução (patch900_*.npk) depois, na mesma pasta patch/.
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
    for p in sorted((game / 'patch').glob('*.npk')):
        shutil.copy2(p, out / 'patch' / p.name)
        print(f'patch/{p.name}: copiado', flush=True)


if __name__ == '__main__':
    main(*sys.argv[1:4])
