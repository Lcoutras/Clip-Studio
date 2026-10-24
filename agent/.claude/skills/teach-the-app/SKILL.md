---
name: teach-the-app
description: Turn everything the Clip Agent learned into Clip Studio itself - propose and (with Luke's OK) apply settings, and write the hand-off report for Claude to build into the app's code and defaults. Use when Luke asks what the agent learned, after several review sessions, or when distill shows proposals.
---

# Teach the app

1. Run `../.venv/Scripts/python.exe tool.py distill`, and read `tool.py feedback` and `../output/agent/knowledge.md`.

2. **Proposed settings.** Explain each one to Luke in plain words, for example "Assist clips: you kept 9 of 10, so they'll rank higher" or "Fail? clips get funny music". Ask him which to apply.
   - Run `tool.py apply-settings` only after he says yes.
   - Settings apply to the next analysis and export, so he should re-analyze in Clip Studio to see them.

3. **Lessons settings can't express.** Look in knowledge.md and your clip notes for things a setting can't capture, for example:
   - "clips that start on the replay intro"
   - "captions that describe the play are weaker than punchlines"
   - "post-match lobby gets detected as a FUNNY MOMENT"

   Write them up as concrete rules for the app: what to detect, what to change, and an example clip (recording + clip number).

4. Run `tool.py handoff`. It writes `../output/agent/handoff.md`. Append a section **"Rules for the app"** with your rules from step 3, each with its evidence and how confident you are.

5. Ask Luke to send `handoff.md` to Claude, in the conversation where Clip Studio is built. Claude will turn it into code and defaults, and the next app update brings them to everyone.
