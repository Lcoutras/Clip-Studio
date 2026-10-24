---
name: review-clips
description: First-line review of the clips Clip Studio suggested. Check each clip's content, timing, caption and music; approve, fix, reject or flag it; log reasons so the app can learn. Use when Luke says to review, check or approve clips, or at the start of a session with no other request.
---

# Review clips

1. **Catch up.** Read `../docs/content-playbook.md` and `../output/agent/knowledge.md` (create it if missing; see CLAUDE.md), then run:
   - `../.venv/Scripts/python.exe tool.py feedback`
   - `../.venv/Scripts/python.exe tool.py distill`

   Note what Luke rejects, and where he overrode you before.

2. **Get the to-do list:** `tool.py todo`.
   - If it's empty, tell Luke to analyze recordings in Clip Studio first, then stop.
   - It shows the top 8 clips per recording by score. Review those. Only use `--top 0` (everything) if Luke asks.

3. **For each clip:**
   1. Run `tool.py sheet "<recording>" <clip>` and **read the image**.
   2. Decide on content, timing, statement and music, as described in CLAUDE.md.
   3. Record everything in **one** `tool.py review` call. Include `--note` with your reasoning, plus any `--caption`, `--tone`, `--music` or `--start/--end` fixes. Examples:
      - `review "2026-10-22 19-22-34.mp4" 3 --approve --caption "Ceiling? Never met her" --music none --note "clean aerial; trending sound fits"`
      - `review "2026-10-22 19-22-34.mp4" 5 --reject not_gameplay --note "post-match lobby screen"`
      - `review "2026-10-22 19-22-34.mp4" 7 --flag --note "can't tell if he touched the ball; check 0:14"`
   4. If you wrote a caption better than anything in the library, save it with `tool.py caption-add`.

4. **Music check.** Run `tool.py music-list`. If a mood folder has fewer than about 5 tracks, or every track has the same energy, use the find-music skill. Ask Luke first if more than a couple of downloads are needed.

5. **Learn.** Add dated lines to knowledge.md, under *Rules I follow*, *Caption style* and *Music*, for patterns you noticed. Then run `tool.py distill`.

6. **Report** to Luke, using the end-of-session format in CLAUDE.md. Remind him that your verdicts show in Clip Studio with a 🤖, and that his Keep/Reject has the final say.
