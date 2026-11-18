"""Extrator de arquivos NPK3 (engine Diesel/Mware da Nitroplus) - Full Metal Daemon Muramasa.

Uso: python npk.py <arquivo.npk> <pasta_saida>
Chave AES-256 recuperada de muramasa_en_GOG.exe (FUN_00401970, montada na pilha).
"""
import hashlib, os, struct, sys
import zstandard
from pathlib import Path
from Crypto.Cipher import AES

KEY = struct.pack('<8I', 0x9e1a8351, 0x5ea8ef69, 0xc251c5e6, 0x0418db08,
                  0x235037b4, 0x5e1fae56, 0x8828b129, 0xa8783e40)


def read_index(f):
    hdr = f.read(32)
    if hdr[:4] != b'NPK3':
        raise ValueError('não é NPK3')
    iv = hdr[8:24]
    count, size = struct.unpack('<II', hdr[24:32])
    idx = AES.new(KEY, AES.MODE_CBC, iv).decrypt(f.read(size))
    p, entries = 0, []
    for _ in range(count):
        flag, nlen = struct.unpack_from('<BH', idx, p); p += 3
        name = idx[p:p + nlen].decode('utf-8'); p += nlen
        fsize, = struct.unpack_from('<I', idx, p); p += 4
        sha = idx[p:p + 32]; p += 32
        nseg, = struct.unpack_from('<I', idx, p); p += 4
        segs = []
        for _ in range(nseg):
            segs.append(struct.unpack_from('<QIII', idx, p)); p += 20
        entries.append((name, flag, fsize, sha, segs))
    return iv, entries


def read_file(f, iv, entry):
    name, flag, fsize, sha, segs = entry
    out = bytearray()
    for off, aligned, stored, orig in segs:
        f.seek(off)
        data = AES.new(KEY, AES.MODE_CBC, iv).decrypt(f.read(aligned))
        # regra do motor: comprimido só se stored < orig; senão são orig bytes crus
        if stored < orig:
            out += zstandard.ZstdDecompressor().decompress(data[:stored], max_output_size=orig)
        else:
            out += data[:orig]
    out = bytes(out)
    if len(out) != fsize or hashlib.sha256(out).digest() != sha:
        raise ValueError(f'{name}: tamanho/sha256 não confere')
    return out


def pkcs7(b):
    n = 16 - len(b) % 16
    return b + bytes([n]) * n


def pack(files, path, seg_size=0x20000, level=19, flags=None):
    """files: {nome_no_npk: bytes}. Grava NPK3 igual aos originais.

    flags: {nome: 0|1}. Flag 1 = arquivo inteiro num segmento (scripts .nut);
    flag 0 = segmentos de 128 KiB. Sem flag informado: 1 para .nut, 0 para o resto.
    """
    iv = os.urandom(16)
    zc = zstandard.ZstdCompressor(level=level)
    metas, blobs = [], []
    for name, data in files.items():
        flag = (flags or {}).get(name, 1 if name.endswith('.nut') else 0)
        step = len(data) or 1 if flag else seg_size
        segs = []
        for i in range(0, len(data), step):  # arquivo vazio: 0 segmentos
            chunk = data[i:i + step]
            comp = zc.compress(chunk)
            # o motor só descomprime se stored < orig; cru grava a tentativa em "stored", como o original
            body = comp if len(comp) < len(chunk) else chunk
            enc = AES.new(KEY, AES.MODE_CBC, iv).encrypt(pkcs7(body))
            segs.append((len(enc), len(comp), len(chunk)))
            blobs.append(enc)
        metas.append((name.encode('utf-8'), flag, data, segs))

    def index(base):
        out, off = bytearray(), base
        for name, flag, data, segs in metas:
            out += struct.pack('<BH', flag, len(name)) + name
            out += struct.pack('<I', len(data)) + hashlib.sha256(data).digest()
            out += struct.pack('<I', len(segs))
            for aligned, stored, orig in segs:
                out += struct.pack('<QIII', off, aligned, stored, orig)
                off += aligned
        return pkcs7(bytes(out))

    isize = len(index(0))  # o tamanho do índice não depende dos offsets
    idx = AES.new(KEY, AES.MODE_CBC, iv).encrypt(index(32 + isize))
    with open(path, 'wb') as f:
        f.write(b'NPK3' + struct.pack('<I', 1) + iv + struct.pack('<II', len(metas), isize))
        f.write(idx)
        for b in blobs:
            f.write(b)


def main(npk, outdir):
    with open(npk, 'rb') as f:
        iv, entries = read_index(f)
        for e in entries:
            dst = Path(outdir) / e[0]
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(read_file(f, iv, e))
        print(f'{npk}: {len(entries)} arquivos -> {outdir}')


if __name__ == '__main__':
    main(*sys.argv[1:3])
