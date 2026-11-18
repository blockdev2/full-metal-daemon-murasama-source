"""Extrai as strings dos scripts Squirrel compilados (media/script/lang/en/*.nut) para JSON.

Uso: python extract_text.py <pasta_lang_en> <pasta_saida_json>
Cada JSON: lista de {"offset", "en", "pt"} na ordem em que aparecem no bytecode.
Preencha "pt" com a tradução; "offset" serve para reinserir depois.
"""
import json, re, struct, sys
from pathlib import Path

# Objeto string no bytecode Squirrel: tag OT_STRING (0x08000010) + u32 tamanho + bytes
STR = re.compile(rb'\x10\x00\x00\x08(....)', re.S)
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*$')


def strings(data):
    pos = 0
    while (m := STR.search(data, pos)):
        n, = struct.unpack('<I', m.group(1))
        start = m.end()
        # shortcut: busca por padrão; um falso positivo no bytecode é descartado por tamanho/UTF-8
        try:
            s = data[start:start + n].decode('utf-8') if start + n <= len(data) else None
        except UnicodeDecodeError:
            s = None
        if s is None:
            pos = m.start() + 1
            continue
        yield m.start(), s
        pos = start + n


def main(src, dst):
    Path(dst).mkdir(parents=True, exist_ok=True)
    total = 0
    for nut in sorted(Path(src).glob('*.nut')):
        data = nut.read_bytes()
        assert data[:4] == b'SCRP' and b'RIQS' in data[:32], nut
        rows = [{'offset': o, 'en': s, 'pt': ''} for o, s in strings(data)
                if not IDENT.match(s) and not s.startswith('media/')]  # pula nomes de função/caminhos
        (Path(dst) / (nut.stem + '.json')).write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
        total += len(rows)
    print(f'{total} strings -> {dst}')


if __name__ == '__main__':
    main(*sys.argv[1:3])
