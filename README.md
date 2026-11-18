# Full Metal Daemon Muramasa — Source & Translation Tools

Reverse-engineered tools for the GOG release of **Full Metal Daemon Muramasa** (JAST USA, v1.04, Nitroplus *Diesel/Mware* engine), plus the decompiled scripts and the extracted game text, ready for fan translation.

The tools unpack the game's encrypted `.npk` archives, extract every script string to JSON, inject translations back, and build patch archives the game loads without touching its original files.

## Contents

| Path | What it is |
|---|---|
| `npk.py` | NPK3 archive reader/writer (extract and pack) |
| `extract_text.py` | Dumps every string from the compiled Squirrel scripts to JSON |
| `inject_text.py` | Writes translations back into the scripts and builds a patch `.npk` |
| `rebuild.py` | Rebuilds a complete playable copy of the game from extracted files |
| `texto/` | 884 JSON files (39,702 strings), one per script, with an empty `pt` field for the translation |
| `jogo_extraido/media/script/` | Decompressed/decrypted scripts (compiled Squirrel bytecode `.nut`) |
| `jogo_extraido/setting.ini`, `jogo_extraido/shader/` | Engine config and compiled shaders |

CGs, voices, sound and video (~3.8 GB) are **not** included. Regenerate them from your own copy of the game (see below).

## Requirements

- Python 3.10+
- `pip install pycryptodome zstandard`
- A legal copy of the game (GOG version 1.04)

## Usage

Extract an archive:

```bash
python npk.py "C:\Program Files\GOG Galaxy\Games\Full Metal Daemon Muramasa\script.npk" jogo_extraido
```

Dump the script text to JSON:

```bash
python extract_text.py jogo_extraido/media/script/lang/en texto
```

Fill in the `pt` field of the strings you translate, then build the patch:

```bash
python inject_text.py jogo_extraido/media/script/lang/en texto patch900_ptbr.npk
```

Copy `patch900_ptbr.npk` into the game's `patch/` folder. Leave `pt` empty for any string you want to keep in English.

Rebuild a full playable copy (original archives untouched):

```bash
python rebuild.py "C:\Program Files\GOG Galaxy\Games\Full Metal Daemon Muramasa" jogo_extraido jogo_reconstruido
```

### Translating

Each JSON entry looks like this:

```json
{"offset": 205, "en": "\r\n//　彼は破壊を求めたわけではない。\r\n  He never wished for destruction. ...", "pt": ""}
```

Lines starting with `//` are the original Japanese, kept as comments. Replace only the English lines, and keep the `\r\n` line breaks and tags such as `<center>`. `inject_text.py` checks that each `en` string still matches the script at `offset`, so it stops instead of corrupting a file.

## File formats

### NPK3 archive

All archives are encrypted with AES-256-CBC. The key is built on the stack in `muramasa_en_GOG.exe` (`FUN_00401970`) and is listed in `npk.py`.

```
header (32 bytes):  "NPK3"  u32 version=1  u8[16] IV  u32 file_count  u32 index_size
index (AES-CBC, PKCS7 padded), per entry:
    u8  flag           1 = whole file in one segment (scripts), 0 = 128 KiB segments
    u16 name_len, name (UTF-8)
    u32 file_size
    u8[32] SHA-256 of the file contents
    u32 segment_count  (0 for empty files)
    per segment: u64 offset, u32 aligned_size, u32 stored_size, u32 original_size
data: each segment is AES-CBC encrypted (same key and IV), PKCS7 padded
```

A segment is zstd-compressed only when `stored_size < original_size`. Otherwise it holds `original_size` raw bytes, and `stored_size` records the size of the rejected compression attempt. The engine relies on this rule, so `npk.pack()` reproduces it exactly.

### Patch loading (`Mware.dll`, class `Mware::CArchiveNPK`)

- At startup the engine mounts the 8 main archives, then every `patch/*.npk`. File names are lowercased and patches are mounted in alphabetical order.
- When the same file name exists in several archives, the patch that sorts last wins. Name a translation patch so it sorts after the official ones, for example `patch900_*.npk`.
- Archive checks: name ends in `.npk`, key length is 32, magic is `NPK3`, version is 1, file names are at most 0x104 bytes. There is no signature or whitelist.
- `setting.ini` is read straight from `system.npk` **before** patches are mounted, so a patch cannot override it.

### Scripts

`.nut` files are compiled Squirrel 3 bytecode inside an `SCRP` wrapper:

```
"SCRP"  u32 16  u32 (size - 20)  u32 (size - 4)  ...bytecode ("RIQS")...  u32 0
```

Strings are stored as `10 00 00 08` (OT_STRING) + u32 length + UTF-8 bytes. Bytecode sections carry counts rather than byte sizes, so strings can change length freely as long as the two `SCRP` size fields are updated. `inject_text.py` does this.

Translatable dialogue lives in `media/script/lang/en/`, separate from the scene logic in `media/script/nut/`.

### Assets

- `.png` files inside the archives are actually **WebP**.
- Most backgrounds and CGs are 1024×576, the engine's native resolution. Some are 2048×1152, and character sprites are 1000–2000 px.
- Audio is Ogg Vorbis and video is WMV.

## Known limitations

- `extract_text.py` finds strings by scanning for their byte pattern, not by fully parsing the bytecode. A false match is very unlikely, since length and UTF-8 checks discard it, but this is not a full parser.
- UI images with English text (under `media/cg/sys/`) have to be edited by hand.
- The bundled font `media/font/droidserif-muramasa.ttf` has not been checked for every accented character a target language needs.
- The engine always renders at 1024×576 and stretches the image. Raising `SCREEN_WIDTH`/`SCREEN_HEIGHT` only changes the coordinate space, so the layout breaks.

## Legal

Full Metal Daemon Muramasa © Nitroplus. English release © JAST USA. This repository holds tools and data for personal fan-translation work. It does not include the game's images, audio or video. Buy the game: <https://www.gog.com/game/full_metal_daemon_muramasa>
