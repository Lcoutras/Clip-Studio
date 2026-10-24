---
name: log-performance
description: Record how posted clips performed (views, likes, shares, watch time, followers) and turn the numbers into lessons. Use when Luke shares stats, screenshots of analytics, or says how a clip did.
---

# Log performance

1. Match each clip Luke mentions to an item in `../.venv/Scripts/python.exe tool.py queue`. If he shares a screenshot, read the numbers from it.

2. Append a row to `../output/agent/performance.csv` for each clip and platform. Create the file with this header if needed:
   `date_logged,item,platform,days_since_post,views,likes,comments,shares,avg_watch_pct,followers_gained,notes`
   Leave unknown fields empty. Never guess numbers.

3. Look for patterns, but only once there are at least 5 posts on a platform, and compare like with like (same platform, similar age). Things to compare:
   - category (goal, save, fail, funny)
   - clip length
   - caption tone (funny or motivational)
   - music or a trending sound
   - posting day and time

4. Write what you find into the **What performs** section of `../output/agent/knowledge.md`, with the date and sample size, e.g. "Funny/fail clips: median 1.8k views vs 600 for goals (n=12, TikTok, Oct-Nov 2026)". Say plainly when a sample is too small to conclude anything.

5. Tell Luke what changes, for example "I'll queue more fails and keep clips under 10s", and ask whether he agrees.
