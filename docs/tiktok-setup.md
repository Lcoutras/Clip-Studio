# Upload exports to TikTok automatically (private app)

**TikTok Uploader.bat** checks `output\Exports\` every 3 hours and sends every new vertical clip to your TikTok **as a draft**, with its description and hashtags. You open the TikTok notification and tap **Post**, and you can add a trending sound first. It remembers what it sent, so nothing goes twice.

It uses your own **private** TikTok developer app:
- It's never submitted for review and never published.
- Nobody can find or use it, and it only works with your own TikTok account.
- You can delete it any time in the developer portal.

## 1. Web pages (already done)
TikTok asks for a privacy policy, terms, and a sign-in return page. They're live at `https://lcoutras.github.io/clip-studio/`, and the site is verified with the file in the `lcoutras.github.io` repository.

## 2. The app (developers.tiktok.com)
1. **Manage apps** → your app (or **Connect an app**). Name it "Clip Studio", category Photo & Video, platform **Web**.
2. URLs:
   - Terms: `https://lcoutras.github.io/clip-studio/terms.html`
   - Privacy: `https://lcoutras.github.io/clip-studio/privacy.html`
   - Web: `https://lcoutras.github.io/clip-studio/`
   - If asked to verify, use **URL prefix** `https://lcoutras.github.io/`.
3. **Save. Don't click "Submit for review".** That's what would make it a public app.
4. Open the **Sandbox** tab and create a sandbox. In the sandbox:
   - **Products:** **Login Kit** (redirect URI `https://lcoutras.github.io/clip-studio/tiktok-callback.html`) and **Content Posting API** with **Upload** on.
   - **Scopes:** `user.info.basic`, `video.upload`.
   - **Target users:** add your TikTok account, then accept the invite in the TikTok app.
5. Copy the sandbox's **Client key** and **Client secret**.

## 3. Tell Clip Studio
Add this to `settings.yaml` in the Clip Studio folder (it stays on your PC; never share the secret):
```yaml
tiktok:
  client_key: "sandbox client key"
  client_secret: "sandbox client secret"
```
The defaults: drafts, a check every 3 hours, no limit on how many clips it sends. To change them, add `watch_minutes:` (e.g. `60` for hourly), a per-check limit with `max_per_run:`, or a daily cap with `max_per_day:`. If TikTok itself says there are too many uploads, the uploader stops and tries again at the next check.

## 4. Run it
Double-click **TikTok Uploader.bat**. The first time, your browser opens TikTok: sign in and allow access. It sends all new clips right away, then checks again every 3 hours. Leave the window open; close it to stop.

To start it with Windows: press Win+R, type `shell:startup`, and put a shortcut to **TikTok Uploader.bat** in that folder.

## What gets uploaded
- Uploads land in your TikTok inbox as drafts; finished, they're regular **posts** (they stay on your profile and go to the For You page). TikTok's upload API can't post to Stories. If the app offers "share to story" when you finish a draft, you can tick it there.
- Every vertical clip in `output\Exports\<date>\`. Highlight compilations and `[horizontal]` versions are skipped.
- The description and hashtags from the clip's `.json` / `.txt`, written by Clip Studio when you export.
- **Once a clip is sent, it moves out of `Exports` into `output\Uploaded\<date>\`** (with its `.txt` and `.json`), so `Exports` only ever holds clips still waiting. Clip Studio knows where it went: **Watch export** still works.
- A play is never sent twice. If you export the same clip again (say with a new caption), the uploader skips it and says so.
- What was sent and when is listed in `output\tiktok\uploaded.json`. To send a clip again, delete its line there and move its files back into `Exports`.

## Finding the drafts on your phone
Each upload arrives as a notification in the TikTok app: **Inbox** (bottom bar) → **System notifications**. Look for one from your app ("RL Clip Studio"), tap it, and post. Not sure where they went? Double-click **TikTok Status.bat**: it asks TikTok what happened to each clip (waiting in your inbox, posted, or failed and why).

## If TikTok says no
- **"too many unfinished drafts in your TikTok inbox"** (`spam_risk_too_many_pending_share`): TikTok only allows a handful of drafts waiting at once. Post or delete the drafts in the TikTok app; the waiting clips go at the next check (or restart the uploader).
- **"daily posting limit"**: TikTok's own cap; the rest go later. Nothing is lost or sent twice.

## Stopping for good
Close the uploader, delete `output\tiktok\`, remove Clip Studio under TikTok → Settings → Security → Manage app permissions, and delete the app in the developer portal.
