---
name: find-music
description: Find and add royalty-free music that is licensed for TikTok, YouTube Shorts and Instagram, sorted into the hype, funny and chill folders. Use when the music library is thin, a mood is missing, or Luke asks for new music.
---

# Find music

1. Run `../.venv/Scripts/python.exe tool.py music-list` to see what's there and what's missing: moods, energy levels, tracks that are too short.

2. **Search** sources that allow reuse in social videos:
   - **Pixabay Music:** Pixabay Content License, no credit needed.
   - **NoCopyrightSounds:** free for YouTube and TikTok with credit.
   - **Free Music Archive:** only CC0 or CC BY tracks.
   - **Incompetech** (Kevin MacLeod): CC BY, credit needed.

   Look for phonk, EDM, trap or rock for **hype**; meme, quirky or comedic for **funny**; lo-fi for **chill**. Rocket League clips usually suit high-energy tracks with a clear drop in the first 30–60 seconds.

3. **For each candidate:**
   1. Open its page and **read the license**. Skip it if the license isn't clear or doesn't allow reuse in online videos.
   2. Find the **direct download link** (an .mp3 or .mp4 address, not the web page). Some sites only give the file after a click or a sign-in. If so, give Luke the page link and ask him to download it, then add it from the file on his PC.
   3. Add it:
      ```
      tool.py music-add "<download link or file path>" --mood hype --title "..." --artist "..." --license pixabay --source-url "<page>" --energy high --notes "big drop at 0:32, aggressive phonk"
      ```
      CC BY and NCS tracks also need `--attribution "Music: <title> by <artist> (<link>)"`. The credit line is added to posts automatically.

4. Add **at most 5 tracks per session**, and keep the moods balanced. Describe each track's feel in `--notes`, since that's what you'll pick by later. You can't listen to it.

5. Tell Luke what you added, from where, under which license, and which tracks need credit.
