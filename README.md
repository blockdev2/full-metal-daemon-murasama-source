# Full Metal Daemon Muramasa — Source & Translation Tools

Reverse-engineered tools for the GOG release of **Full Metal Daemon Muramasa** (JAST USA, v1.04, Nitroplus *Diesel/Mware* engine), plus the decompiled scripts and the extracted game text, ready for fan translation.

The tools unpack the game's encrypted `.npk` archives, extract every script string to JSON, inject translations back, and build patch archives the game loads without touching its original files.

> **Machine translation included.** `translate.py` is an interactive terminal app that translates the whole script scene by scene, with context, the Japanese original and a glossary. Configure it with either:
> - a **Google Gemini API key**, or
> - a local **[OpenCode](https://opencode.ai)** install, which works with OpenCode's free models (for example `deepseek-v4.1-flash-free`).
>
> Run `python translate.py`, press `s`, pick a provider and model, then press `t` (one scene) or `a` (everything). Progress is crash-safe and resumable. See [Machine translation](#machine-translation-gemini--opencode).

## Contents

| Path | What it is |
|---|---|
| `npk.py` | NPK3 archive reader/writer (extract and pack) |
| `extract_text.py` | Dumps every string from the compiled Squirrel scripts to JSON |
| `inject_text.py` | Writes translations back into the scripts and builds a patch `.npk` |
| `rebuild.py` | Rebuilds a complete playable copy of the game from extracted files |
| `translate.py`, `translator/` | Interactive terminal app that machine-translates `texto/` with Google Gemini |
| `glossary.txt` | Fixed terms sent to Gemini with every request |
| `texto/` | 884 JSON files (39,702 strings), one per script, with an empty `pt` field for the translation |
| `jogo_extraido/media/script/` | Decompressed/decrypted scripts (compiled Squirrel bytecode `.nut`) |
| `jogo_extraido/setting.ini`, `jogo_extraido/shader/` | Engine config and compiled shaders |

CGs, voices, sound and video (~3.7 GB) are too large for git. They are attached to the **[assets-v1.04 release](https://github.com/blockdev2/full-metal-daemon-murasama-source/releases/tag/assets-v1.04)** as zips (see [Getting the assets](#getting-the-assets)).

## Getting the assets

`rebuild.py` needs the full extracted tree. Choose one option.

**Option A: download the release zips**

| Zip | Extracts to | Size |
|---|---|---|
| `assets-cg.zip` | `media/cg/` (backgrounds, CGs, sprites, UI) | 1.4 GB |
| `assets-voice.zip` | `media/voice/` | 1.5 GB |
| `assets-video.zip` | `media/dx/` | 0.6 GB |
| `assets-sound.zip` | `media/sound/` (BGM, SE) | 0.2 GB |
| `assets-misc.zip` | `media/font/`, `media/particle/` | < 1 MB |

Extract **every** zip into `jogo_extraido/` (each zip already contains the `media/...` paths):

```bash
gh release download assets-v1.04 --repo blockdev2/full-metal-daemon-murasama-source --dir zips
```
```powershell
Get-ChildItem zips\*.zip | ForEach-Object { Expand-Archive $_ -DestinationPath jogo_extraido -Force }
```

The zips already include the official patches 1.01–1.04.

**Option B: extract from your own game install**

```powershell
$g = "C:\Program Files\GOG Galaxy\Games\Full Metal Daemon Muramasa"
Get-ChildItem "$g\*.npk", "$g\patch\*.npk" | ForEach-Object { python npk.py $_.FullName jogo_extraido }
```

Archives in `patch/` must come last so the official fixes overwrite the original files.

Either way, `rebuild.py` then produces a full playable copy (see [Usage](#usage)).

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

## Machine translation (Gemini / OpenCode)

`translate.py` is an interactive terminal app that fills in the `pt` fields of `texto/*.json` with Google Gemini or OpenCode, one scene at a time, so the model sees who is speaking and what came before.

```bash
pip install textual google-genai   # google-genai is only needed for the Gemini provider
python translate.py
```

```
 Muramasa Translator                gemini-3.8-flash · 10 req/min · saved key ••••a1b2 · idle
 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   31%  11,027 / 35,573 strings
  / filter scenes            ▎  ma01_001  ·  6 / 8 strings
                             ▎
     scene          done     ▎  EN  He never wished for destruction.
  ✓  ma00_000       215/215  ▎  PT  Ele nunca desejou a destruição.
  ◐  ma01_001       6/8      ▎
  ·  ma01_002       0/97     ▎  EN  "I remember the clear blue sky..."
                             ▎  PT  —
 ──────────────────────────────────────────────────────────────────────────────
  12:01:12  ✓ ma00_000  +215 strings
  12:02:40  ! 429 from Gemini, retrying in 20s
  t Translate scene  a Translate all  p Pause  s Settings  / Filter  q Quit
```

### Providers

Choose the provider in Settings (`s`). The **Model** list loads from the chosen provider.

| Provider | Needs | Notes |
|---|---|---|
| **Google Gemini** | An API key | The free tier is very limited, around 20 requests/day on Flash and 500/day on Flash-Lite |
| **OpenCode** | [OpenCode](https://opencode.ai) installed and signed in | Uses OpenCode's models, including the free ones such as `opencode/deepseek-v4.1-flash-free` |

OpenCode's free models may only be used through OpenCode itself. The tool therefore drives your local `opencode` CLI (`opencode run`) and does not call the API directly. Each request goes in a temp file attached with `--file`. The CLI runs in an empty private folder, so the agent never sees your files. It runs with `--standalone`, so the OpenCode app does not need to be open; it only has to be signed in once. The CLI is found on `PATH`, or in the OpenCode desktop app's `resources\opencode-cli.exe`, or at the path you set in Settings. Free OpenCode models are offered for a limited time, and each has its own data-retention terms (see opencode.ai/docs/zen).

**Which OpenCode model is billed where.** The model's prefix decides who pays, and the Model list labels each one:

| Prefix / label | Billed to | Example |
|---|---|---|
| `opencode-go/…` · **Go plan** | Your OpenCode Go subscription | `opencode-go/deepseek-v4.1-flash` |
| `opencode/…-free` · **free** | Free quota (limited, can run out) | `opencode/deepseek-v4.1-flash-free` |
| `opencode/…` · **pay-as-you-go credits** | Zen credits, separate from Go | `opencode/deepseek-v4.1-flash` |

With a Go subscription, pick a **Go plan** model. A look-alike `opencode/…` model fails with `402 Insufficient balance`, and the app then names the matching `opencode-go/…` model. Some Go models (DeepSeek, Qwen, GLM, Kimi) are hosted outside the EU/US. To use them, enable **Global** in the OpenCode console under Settings › Privacy › Regions. Billing and region errors stop the queue at once instead of retrying.

### Setup (Gemini)

1. Create a free API key at <https://aistudio.google.com/apikey>.
2. Press `s` in the app, paste the key, then press **Test key**. The key is saved to `translator_config.json`, which is git-ignored. You can set the `GEMINI_API_KEY` environment variable instead.
3. Set **Requests per minute** below your free-tier limit. The free tier has per-minute and per-day quotas. The app paces requests and retries on `429` automatically.

### Keys

| Key | Action |
|---|---|
| `↑` `↓` | Select a scene (preview on the right) |
| `t` | Translate the selected scene |
| `a` | Translate every pending scene (respects the current filter) |
| `p` | Pause after the current request |
| `c` | Continue an unfinished (paused or interrupted) session |
| `h` | Session & recovery panel |
| `s` | Settings: API key, model, rate, glossary |
| `/` | Filter scenes by name; `Enter` goes back to the list |
| `r` | Reload `texto/` from disk |
| `q` | Quit (progress is already saved) |

`python translate.py --status` prints progress without opening the interface.

### How it stays faithful

- The model receives a whole scene in chunks of up to 40 strings, plus the last translated lines as context and the Japanese original (`//` lines) as reference.
- `glossary.txt` holds fixed terms (one `term = translation` per line) and is sent with every request.
- Every response is validated before it is saved. It must have the same number of lines, the same tags (`<center>`…) and the same placeholders (`%s`). `<br>` is the exception: it is a manual line break placed for English word wrapping, so the model may drop it or move it. Models that rename the `pt` field (for example to `lines`) or return one string instead of a list are normalized, but an echo of the English is never accepted. Invalid answers are retried, and strings that keep failing are skipped and retried on the next run.
- Indentation, blank lines, Japanese comments and line endings are never sent to the model. The tool re-applies them itself, so every `pt` stays compatible with `inject_text.py`.
- Each scene is saved atomically after every chunk. You can quit or pause at any time, and strings that already have a `pt` are never re-sent.

### Crash safety and resuming

Every translated chunk is saved in three steps, each flushed to disk before the next one:

1. **Journal** (`translator_journal.jsonl`): each translation is appended with its scene, row and original English.
2. **Scene file** (`texto/*.json`): written to a temp file, fsynced, then renamed over the original, so a scene is never half-written.
3. **Checkpoint** (`translator_state.json`): the running queue, current scene, strings done and strings that failed.

If the app or the PC dies at any point, the next start does two things:

- **Replays the journal.** Any translation that reached the journal but not its scene file is restored. An entry is applied only if the row is still empty and its English still matches, so it can never land on the wrong line. A journal line cut off mid-write is ignored.
- **Offers to continue.** It shows the unfinished session in the log and the header. Press `c` to resume from the scene where it stopped.

The **Session & recovery** panel (`h`) shows the status, start and last-save times, current scene, scenes left, strings done, failed strings per scene, and the journal's size. From it you can:

| Button | Effect |
|---|---|
| Continue | Resume the unfinished session |
| Discard session | Forget the queue (keeps the failed list) |
| Clear failed list | Reset the failed-strings report |
| Delete journal | Remove the recovery journal |

The three destructive buttons need a second press to confirm. None of them touches translations already saved in `texto/`. The journal and checkpoint are local and git-ignored.

Gemini's free tier may use your prompts to improve Google's models. Machine translation still needs human review, especially for jokes, wordplay and archaic speech.

### Tests

```bash
pip install pytest pytest-asyncio
python -m pytest tests
```

The tests run headless with a fake Gemini client and cover line rebuilding, CRLF/LF handling, validation, chunking, an end-to-end scene translation driven by key presses, small-terminal handling and saving settings. They also cover crash recovery: journal replay (including a line cut mid-write and an entry whose English no longer matches), checkpoint round-trip and corrupt state files, resuming an interrupted session with `c`, and two-step confirmation in the session panel.

## Known limitations

- `extract_text.py` finds strings by scanning for their byte pattern, not by fully parsing the bytecode. A false match is very unlikely, since length and UTF-8 checks discard it, but this is not a full parser.
- UI images with English text (under `media/cg/sys/`) have to be edited by hand.
- The bundled font `media/font/droidserif-muramasa.ttf` has not been checked for every accented character a target language needs.
- The engine always renders at 1024×576 and stretches the image. Raising `SCREEN_WIDTH`/`SCREEN_HEIGHT` only changes the coordinate space, so the layout breaks.

## Legal

Full Metal Daemon Muramasa © Nitroplus. English release © JAST USA. This repository holds tools and data for personal fan-translation work. Game images, audio and video are attached to a release for personal use only — keep this repository private. Buy the game: <https://www.gog.com/game/full_metal_daemon_muramasa>
