---
name: process-recordings
description: Find new gaming recordings, analyze them with Clip Studio, pick the clips worth posting, write per-platform posts and put them in the Ready to post queue. Use when Luke says to process, check, or clip his recordings, or at the start of a session with no other request.
---

# Process recordings

1. **Catch up.** Read `../docs/content-playbook.md` and `../output/agent/knowledge.md` (create it if missing; see CLAUDE.md), then run:
   - `../.venv/Scripts/python.exe tool.py status`
   - `../.venv/Scripts/python.exe tool.py feedback`
   Note which kinds of clips Luke rejects.

2. **Ask about the queue first.** If there are packaged clips that aren't marked as posted, ask Luke which ones he posted and where. Record each with `tool.py posted`. Don't hold up the rest of the work waiting for his answer.

3. **Analyze new recordings,** newest first, one at a time:
   - Run `tool.py scan "<full path>"` **in the background**, because it can take 10-20 minutes per hour of video.
   - Use `--fast` only if Luke asked for speed, or if there are more than 3 recordings waiting.
   - If a recording isn't Rocket League (no clips found, or the notes say no scoreboard was seen), say so in your report and move on.

4. **Judge each suggested clip.** Skip ones already REJECTED by Luke, and pass over clip types his feedback says he dislikes. For the rest:
   - Run `tool.py sheet "<recording>" <clip>` and **read the image**.
   - Judge it with the checklist in CLAUDE.md (hook, payoff, his play, story, variety).
   - Record the verdict:
     - `tool.py review ... --keep --note "<why>"` for clips worth posting
     - `--reject <reason> --note "<why>"` for clips that clearly aren't
     - `--flag --note "<what to check>"` when you can't tell

5. **Package the best ones,** at most about 3 per recording. For each, write `../output/agent/posts/<recording-stem>_clip<N>.json` (fields in CLAUDE.md), then run `tool.py package "<recording>" <clip> --post <that file>`. If package rejects the JSON, fix it and run it again.

6. **Learn.** Add dated lines to knowledge.md for anything new: patterns in what you kept, Luke's comments in this session, settings you'd suggest.

7. **Report** to Luke (format in CLAUDE.md). Include the path to `../output/ready/` and remind him that each folder's POST.md has the text to copy.
