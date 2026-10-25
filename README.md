# Rocket League Clipper

Point it at your Rocket League recordings and it finds the best plays and saves each one as its own edited vertical clip (TikTok / Shorts / Reels), ready to post:

- **Finds the plays** by reading the game's on-screen popups (EPIC SAVE, AERIAL GOAL, DEMOLITION, HAT TRICK...) and listening for goal explosions. Plays are ranked, so only the best are kept.
- **Removes your voices.** It uses the game-only audio track, or AI voice removal for older recordings.
- **Adds music that hits on the drop.** The play lands exactly on the song's drop, the music dips and the goal explosion punches through.
- **Adds captions**: funny puns or motivational lines. They come from a built-in library, or Claude looks at the clip and writes one about what actually happened.
- **Adds edits**: slow-mo into the moment, a zoom punch on impact, a big "EPIC SAVE!" stamp, a blurred-background vertical layout, and an optional @handle watermark.

## 1. Setup (Windows, one time)

1. Download this repo (green **Code** button > **Download ZIP**) and unzip it somewhere, e.g. `Documents\rl-clipper`.
2. Double-click **`setup.bat`**. It installs ffmpeg and Python if needed and creates the **Clip Studio** desktop shortcut. If it asks, close the window and run it again.
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

## 3. Clip Studio (the app)

Open **Clip Studio** from the shortcut that `setup.bat` puts on your desktop, or double-click `Clip Studio.vbs` in this folder. It opens in its own window with no console, and all the work runs in the background, with progress shown in the status meter at the top right. Everything stays on your PC.

**Import → Analyze → Review → Edit → Export**

1. **Import:** drag one or more recordings (or a folder) onto the window, or click **Import** to browse. Analysis starts on its own, one recording after another.
2. **Analyze:** Clip Studio reads the game's popups, listens for goal explosions, and picks up reactions on your mic track. A reaction right after a play stays in that clip, and replays don't become separate clips. A reaction on its own only becomes a clip when something happened in the game just before it (a hit, explosion or demo), and at most about 3 per 10 minutes (the loudest), so chatting doesn't fill the list.
3. **Review:** the recording menu at the top starts on **All recordings**, so clips from every recording show up in one list as each one finishes analyzing (a ⏳ row at the top shows what's still running). Each clip says which recording it's from, and clicking it loads that recording in the player. Pick a single recording in the menu to see only its clips. Suggested clips are labeled **Goal, Assist, Save, Epic Save, Clear, Center, Aerial, Demo, Post, Blooper/Fail** with the reason each was picked.
   - Click a category chip to hide or show that type. Captions follow the category: change a clip's category (e.g. to **Post** or **Fail?**) and 🎲 picks lines for that type.
   - **Every goal** becomes a Goal clip, whoever scored. The reason says who: you, a teammate, or the other team.
   - **Clears, centers and aerial touches** only become clips when the game audio shows a big hit, and only the best few per match.
   - **Your voice never makes a clip on its own.** A reaction only extends a play the game showed.
   - **Blooper/Fail** is for whiffs, own goals and funny mistakes. Switch a clip to it with the category menu.
   - Missed something? Scrub the **Whole recording** bar and click **+ Add moment here**.
4. **Edit** (right panel):
   - **Timing:** drag the blue edges on the clip timeline, nudge with **−/+**, or set the start or end at the playhead.
   - **Caption** (🎲 gives another one), **Music**, **Game/Music volume**, and **Framing** (Full view or Zoom to fill, with a position slider).
   - **Kept / Rejected** decides whether the clip gets exported. When you reject a clip, pick why: **Not gameplay**, **Not my play**, **Nothing happened**, **Bad timing**, **Duplicate** or **Other**. See *Clip Studio learns from you* below.
   - **🚩 Flag** (or press **F**) marks a clip to come back to, with an optional note. Tick **🚩 Flagged only** in the list to see just those. The recording menu shows how many are waiting ("🚩 3 to review"), and flags survive re-analysis.
5. **Export:** **Export this clip** saves a single clip. **Export kept clips** saves all of them. **Build highlight video** joins the kept clips into one video. In **All recordings** it uses kept clips from every recording, in recording order.

**Clip Studio learns from you.** Your rejections, their reasons, and the clips you export are remembered on your PC (`output\feedback.json`, kept through updates) and change the next analysis:
- A kind of clip you keep rejecting (for example voice reactions on their own, or other players' goals) is ranked lower once you've rejected it 3 times and more often than you kept it.
- A clip rejected as **Not gameplay** teaches it what that screen (a menu, a lobby, a loading screen) looks like, and moments that look the same are skipped.
- If you keep trimming one type of clip the same way, say 2 seconds more lead-in on goals, that becomes the default for that type (after 3 exported clips).
- **Bad timing** and **Duplicate** don't count against that kind of clip.
- Rejected clips stay rejected when a recording is analyzed again.

**What Clip Studio learned** (under the clip list) shows the current rules and has **Forget everything**. **Save feedback report** writes `output\feedback_report.txt`. Share it with Claude to improve the built-in defaults for future versions.

**Analysis speed:** the menu next to **Analyze** offers **Standard** (checks the screen every second) and **Fast** (every 2 seconds, about twice as quick; a popup shown for under 2 seconds can be missed). Either way, stretches where the screen doesn't change (menus, loading screens, pauses) are skipped automatically.

**Cancel and remove:** the **Cancel** button next to the status meter stops the running task. In the task list (click the status meter), every waiting or running task has its own Cancel/Remove button. **Remove** next to Analyze takes a recording off the list. Your video file and any exported clips stay on your PC. Cancelling a re-analysis keeps the previous results.

**Updates:** when a new version is published, a green **⬆ Update to …** button appears at the top. Click it and Clip Studio downloads the update and restarts. Your clips, music, fonts and `settings.yaml` are kept. You can also check yourself from the task list (**Check for updates**).

Keyboard: **Space** play/pause, **↑/↓** previous/next clip, **P** play clip, **I/O** set start/end, **Delete** reject/keep, **F** flag.

Exports go to **`output\Exports\<today's date>\`**, e.g. `output\Exports\2026-10-24\Goal - Called it from kickoff (Oct 22 6.25 PM #3).mp4`. Each name says what the play is, its caption, and which recording it came from. Every clip has a `.txt` next to it with the title and hashtags to paste. Highlight videos go in the same day's folder. The **Open output folder** link opens today's folder. Clip Studio's own working files (analysis, the captions sheet, raw cuts) are kept separately in `output\Analysis\`, and re-analyzing a recording never deletes your exports.

MKV recordings can be analyzed and exported, but they can't be previewed in the player. Use OBS **File → Remux Recordings** to make an MP4.

## Upload to TikTok automatically (optional)

**`TikTok Uploader.bat`** watches `output\Exports\` and uploads each new clip to your TikTok with its caption and hashtags. It needs a free TikTok developer app; the 15-minute setup is in [docs/tiktok-setup.md](docs/tiktok-setup.md).

## Clip Agent (optional, temporary)

**`Clip Agent.bat`** opens Claude Code as a first-line reviewer of Clip Studio's clips:
- It checks the footage, caption, music and timing of each suggested clip, and fixes or rejects what's off. Its verdicts show here with a 🤖.
- It adds better caption lines and properly licensed music.
- It turns what it learns into settings for this app, and into a hand-off report for Claude to build into the app itself.

It runs on your Claude subscription (no API bill) and never posts anything. See [agent/README.md](agent/README.md).

## Rocket League replays (recommended)

Save the replay after each match you record: on the end-of-match screen choose **Save Replay**. Rocket League puts replays in `Documents\My Games\Rocket League\TAGame\Demos` (Steam) or `DemosEpic` (Epic Games). Clip Studio looks in both automatically, even when Documents is in OneDrive. You can also import `.replay` files with **Import**.

When a recording has a matching replay, Clip Studio uses the replay's exact record of the match instead of reading the screen:
- your goals (aerial goals marked), saves and epic saves, demos, post hits, and every assist on a teammate's goal
- goals against you as **Fail?** suggestions
- the real **shot speed** on the clip ("107 KM/H") and in the post title

The replay is lined up with the recording using the time it started (from the file name, like OBS's `2026-10-22 23-44-09.mp4`), then fine-tuned by matching goals to the "SCORED!" banners on screen. A green note above the clip list says which replays were used.

Clip Studio reads replays with [rrrocket](https://github.com/nickbabcock/rrrocket), a free, open-source tool. It downloads itself into `tools\` the first time it's needed.

## 4. Make clips without the app

Drag a recording, or a whole folder of them, onto **`clip.bat`**. Finished edits go to `output\Exports\<today's date>\`. The caption/music sheet (`captions.csv`, see below) and the unedited cuts (`raw\`) are in `output\Analysis\<video name>\`.

A 1-hour recording takes roughly 10–20 minutes to scan on a typical PC, then about 30 seconds per clip to render. Set `encoder: h264_nvenc` under `render:` in `settings.yaml` on an NVIDIA GPU for much faster renders.

### Change captions, music or timing

Open `output\Analysis\<video>\captions.csv` in Excel. Per clip you can:
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

## 5. AI captions (optional)

Without setup, captions come from `captions\library.yaml`: about 100 Rocket League puns and motivational lines, sorted by play type. Edit or extend it freely.

To have Claude write a caption for each clip based on what happens in it:

1. Get an API key at https://console.anthropic.com (it costs a few cents per clip).
2. Open Command Prompt and run: `setx ANTHROPIC_API_KEY "sk-ant-...your key..."`
3. Open a new window and run the clipper as usual.

Each clip then gets an AI caption in `caption` and a library caption in `alt_caption`, so you can choose either one.

## 6. Tuning

Your own settings go in `settings.yaml` (create it next to `README.md`). List only what you want to change; `settings.example.yaml` shows every option with its default. Updates never touch `settings.yaml`. The ones you'll use most:

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
