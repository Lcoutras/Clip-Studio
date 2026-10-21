# Rocket League Clipper

Point it at your Rocket League recordings and it finds the best plays and saves each one as its own edited vertical clip (TikTok / Shorts / Reels), ready to post:

- **Finds the plays** by reading the game's on-screen popups (EPIC SAVE, AERIAL GOAL, DEMOLITION, HAT TRICK...) and listening for goal explosions. Plays are ranked, so only the best are kept.
- **Removes your voices.** It uses the game-only audio track, or AI voice removal for older recordings.
- **Adds music that hits on the drop.** The play lands exactly on the song's drop, the music dips and the goal explosion punches through.
- **Adds captions**: funny puns or motivational lines. They come from a built-in library, or Claude looks at the clip and writes one about what actually happened.
- **Adds edits**: slow-mo into the moment, a zoom punch on impact, a big "EPIC SAVE!" stamp, a blurred-background vertical layout, and an optional @handle watermark.

## 1. Setup (Windows, one time)

1. Download this repo (green **Code** button > **Download ZIP**) and unzip it somewhere, e.g. `Documents\rl-clipper`.
2. Double-click **`setup.bat`**. It installs ffmpeg and Python if needed. If it asks, close the window and run it again.
3. **Older recordings with your voice mixed in:** also run **`setup-voice-removal.bat`** (large download).
4. **Music:** drop tracks into `music\hype`, `music\funny` and `music\chill` (see `music\README.md` for copyright-safe sources).

## 2. Record so voices are separate (OBS)

Then new recordings won't need AI voice removal and the game audio stays perfectly clean:

1. **Settings > Output**, set *Output Mode* to **Advanced**. On the **Recording** tab, tick audio tracks **1 and 2**. Use **mkv** as the format and remux to mp4 afterwards (File > Remux), or use mp4 directly.
2. **Edit > Advanced Audio Properties**:
   - *Desktop Audio* (game): tracks **1** and **3** only
   - *Mic/Aux* and *Discord* (voices): tracks **2** and **3** only
3. **Settings > Output > Streaming** tab, set *Audio Track* to **3** so the stream still has everyone.

Track 1 is then game-only, and the clipper picks it automatically.

## 3. Make clips

Drag a recording, or a whole folder of them, onto **`clip.bat`**. Results go to `output\<video name>\`:

```
vertical\    finished edits to post
raw\         the unedited play, voices removed
captions.csv the caption/music sheet (see below)
```

A 1-hour recording takes roughly 10–20 minutes to scan on a typical PC, then about 30 seconds per clip to render. Set `encoder: h264_nvenc` in `config.yaml` on an NVIDIA GPU for much faster renders.

### Change captions, music or timing

Open `output\<video>\captions.csv` in Excel. Per clip you can:
- rewrite `caption` (or paste in `alt_caption`, a second option)
- set `music` to a file name from your music folder
- adjust `start` / `end` (seconds into the recording)
- put `yes` in `skip` to drop the clip

Save, then drag the same recording onto **`render.bat`**.

### Flag a play yourself

Create a text file next to the recording with the same name plus `.markers.txt`, e.g. `game.mp4` > `game.markers.txt`, with one timestamp per line:

```
12:34 nasty flick
1:02:10
```

Those moments are always clipped.

## 4. AI captions (optional)

Without setup, captions come from `captions\library.yaml`: about 100 Rocket League puns and motivational lines, sorted by play type. Edit or extend it freely.

To have Claude write a caption for each clip based on what happens in it:

1. Get an API key at https://console.anthropic.com (it costs a few cents per clip).
2. Open Command Prompt and run: `setx ANTHROPIC_API_KEY "sk-ant-...your key..."`
3. Open a new window and run the clipper as usual.

Each clip then gets an AI caption in `caption` and a library caption in `alt_caption`, so you can choose either one.

## 5. Tuning

All settings live in `config.yaml`, with comments. The ones you'll use most:

| Want | Change |
|---|---|
| fewer, better clips | raise `min_score` (e.g. 7 = only epic saves, aerials and better) |
| more clips | lower `min_score` to 4, or `audio_peak_db` to 8 |
| your goals ranked higher | `player_names: ["YourName"]` |
| zoomed-in vertical instead of blurred bars | `vertical_style: crop` |
| YouTube versions too | `format: both` |
| watermark | `watermark: "@yourhandle"` |

**If popups aren't being detected** (custom HUD, unusual resolution, or a webcam over the middle of the screen), find a moment where a popup is on screen and run:

```
.venv\Scripts\python -m rlclip calibrate "C:\path\game.mp4" --at 12:34
```

It saves a screenshot with the scanned area outlined in yellow and prints what it read. If needed, adjust `ocr_region`.

## Command line

```
.venv\Scripts\python -m rlclip run <video-or-folder> [--format vertical|horizontal|both] [--rescan] [--recaption]
.venv\Scripts\python -m rlclip scan <video-or-folder>      # detect + captions.csv only
.venv\Scripts\python -m rlclip render <video-or-folder>    # render from captions.csv
.venv\Scripts\python -m rlclip calibrate <video> --at mm:ss
```
