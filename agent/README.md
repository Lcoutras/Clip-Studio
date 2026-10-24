# Clip Agent

A temporary helper: Claude Code working as the **first-line reviewer** of Clip Studio's clips. Its real job is to learn what makes a good clip and **build that into Clip Studio**, so the app gets it right on its own.

## How it works
1. **You** analyze recordings in Clip Studio as usual.
2. **The agent** reviews each suggested clip: footage, timing, caption and music. It approves, fixes, rejects or flags each one, with a note explaining why. Its verdicts show in Clip Studio with a 🤖.
3. **The agent** adds better caption lines to the library (`captions\my_captions.yaml`) and downloads royalty-free music into your `music` folders, keeping each track's license on file.
4. **You** have the final say: Keep/Reject in Clip Studio, then export. When you overrule the agent, it learns from that.
5. **Teaching the app** (`/teach-the-app`): the agent turns its decisions into settings Clip Studio applies, but only when you say yes. It also writes `output\agent\handoff.md`. **Send that file to Claude** (in the chat where Clip Studio is built) so the lessons become the app's built-in defaults.

## One-time setup
1. Run `setup.bat` (Clip Studio itself) if you haven't already.
2. Install Claude Code. In **PowerShell**:
   ```
   irm https://claude.ai/install.ps1 | iex
   ```
   If it says Git is needed, run `winget install Git.Git`, then the line above again.
3. Double-click **`Clip Agent.bat`**. The first time:
   - sign in with your Claude account
   - when it asks whether you trust this folder, choose **Yes**. Until you do, Claude Code ignores the agent's pre-approved commands and asks about every step.

## Using it
Analyze some recordings in Clip Studio, then double-click **Clip Agent.bat**. It starts with `/review-clips`. You can also ask it things like:
- "find more hype music" (runs `/find-music`)
- "what have you learned?" or "teach the app" (runs `/teach-the-app`)
- "why did you reject clip 4?"
- "make posts for the clips I exported": writes captions for TikTok, YouTube and Instagram into `output\ready\`

It runs on your Claude subscription, so there's no API bill. Each clip it looks at costs a little usage, which is why it reviews the top 8 clips per recording by default. Analysis and exports run in Clip Studio on your PC and cost nothing.

## Where things are
| Path | What |
|---|---|
| `output\agent\decisions.jsonl` | every verdict and edit the agent made (the raw lessons) |
| `output\agent\knowledge.md` | its notes: your taste, its rules, caption style, music |
| `output\agent\proposed_settings.yaml` | settings it suggests; applied only when you agree |
| `output\agent\handoff.md` | the report to send to Claude |
| `captions\my_captions.yaml` | caption lines you or the agent added (updates never touch it) |
| `music\catalog.csv` | where each added track came from, its license and credit line |
