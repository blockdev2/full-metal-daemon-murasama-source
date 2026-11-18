"""Reinsere as traduções (campo "pt" dos JSONs) nos .nut e gera um patch NPK3.

Uso: python inject_text.py <pasta_lang_en_original> <pasta_json> <saida.npk>
Copie a saída para <jogo>/patch/ (o jogo carrega patch/*.npk).
Só entram no patch os scripts com pelo menos um "pt" preenchido.
"""
import json, struct, sys
from pathlib import Path
import npk

TAG = b'\x10\x00\x00\x08'


def inject(data, rows):
    out, pos = bytearray(), 0
    for r in sorted((r for r in rows if r['pt']), key=lambda r: r['offset']):
        o = r['offset']
        n, = struct.unpack_from('<I', data, o + 4)
        old = data[o + 8:o + 8 + n]
        if data[o:o + 4] != TAG or old.decode('utf-8') != r['en']:
            raise ValueError(f'offset {o}: string original não confere (JSON de outra versão?)')
        new = r['pt'].encode('utf-8')
        out += data[pos:o] + TAG + struct.pack('<I', len(new)) + new
        pos = o + 8 + n
    out += data[pos:]
    # cabeçalho SCRP: [magic][16][tamanho-20][tamanho-4]
    struct.pack_into('<II', out, 8, len(out) - 20, len(out) - 4)
    return bytes(out)


def main(src, jsondir, outnpk):
    files = {}
    for js in sorted(Path(jsondir).glob('*.json')):
        rows = json.loads(js.read_text(encoding='utf-8'))
        if not any(r['pt'] for r in rows):
            continue
        name = f'{js.stem}.nut'
        files[f'media/script/lang/en/{name}'] = inject((Path(src) / name).read_bytes(), rows)
    if not files:
        sys.exit('nenhuma tradução preenchida')
    npk.pack(files, outnpk)
    print(f'{len(files)} scripts -> {outnpk}')


if __name__ == '__main__':
    main(*sys.argv[1:4])
