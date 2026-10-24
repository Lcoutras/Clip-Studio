# Clip Agent

Claude Code working as your clip manager. It:
- finds new recordings
- analyzes them with Clip Studio
- looks at the suggested clips and picks the ones worth posting
- writes a caption, title and hashtags for each platform
- fills **`output\ready\`** with finished clips, each in its own folder with a `POST.md` of text to copy

You do the uploading. In this first phase the agent never posts anything.

## One-time setup
1. Run `setup.bat` (Clip Studio itself) if you haven't already.
2. Install Claude Code. Open **PowerShell** and run:
   ```
   irm https://claude.ai/install.ps1 | iex
   ```
   If it says Git is needed, run `winget install Git.Git` and then the line above again.
3. Close PowerShell. Double-click **`Clip Agent.bat`**. The first time:
   - sign in with your Claude account
   - when it asks whether you trust this folder, choose **Yes**. Until you do, Claude Code ignores the agent's pre-approved commands and asks about every step.

The agent runs on your Claude subscription, so there's no API bill. A long session counts toward your plan's usage limits, so if you hit a limit, wait for it to reset.

## Using it
Double-click **Clip Agent.bat**. It starts with `/process-recordings`. After that it's a chat, so you can talk to it:
- "only do yesterday's recordings"
- "I posted the save clip on TikTok and YouTube"
- "here are my TikTok stats" (paste numbers or drop a screenshot) runs `/log-performance`
- "what's trending in Rocket League clips right now?" runs `/refresh-research`
- "why did you skip clip 4?"

It asks before running anything it isn't pre-approved for. It's pre-approved for its own Clip Studio commands, reading your clips and notes, writing in `output\agent\`, and web research.

## Where things are
| Path | What |
|---|---|
| `output\ready\<item>\` | the clip `.mp4` + `POST.md` (TikTok, YouTube and Instagram text, plus a checklist) |
| `output\agent\knowledge.md` | what the agent has learned: your taste, what performs, trends |
| `output\agent\posted.csv`, `performance.csv` | what was posted where, and how it did |
| `agent\CLAUDE.md` | the job description. Edit it to change how the agent works. Note: updates replace this folder, so put personal preferences in `knowledge.md` instead. |

The agent looks for recordings in your **Videos** folder and its subfolders (from the last 14 days, at least 2 minutes long). To change that, add to `settings.yaml`:
```yaml
agent:
  watch_dirs: ["~/Videos", "D:/Recordings"]
  max_age_days: 30
```

Clips the agent reviews show up in Clip Studio with its note ("Agent: ..."). Your own Keep/Reject in Clip Studio always wins, and only your rejections teach the detector.
