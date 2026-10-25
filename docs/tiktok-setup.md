# Upload exports to TikTok automatically (private app)

**TikTok Uploader.bat** checks `output\Exports\` once a day and sends each new vertical clip to your TikTok **as a draft**, with its description and hashtags. You open the TikTok notification and tap **Post**, and you can add a trending sound first. It remembers what it sent, so nothing goes twice.

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
The defaults: drafts, checks once a day, at most 5 clips a day. To change them, add `watch_minutes:` (e.g. `60` for hourly) or `max_per_day:`.

## 4. Run it
Double-click **TikTok Uploader.bat**. The first time, your browser opens TikTok: sign in and allow access. It sends anything new right away, then checks once a day. Leave the window open; close it to stop.

To start it with Windows: press Win+R, type `shell:startup`, and put a shortcut to **TikTok Uploader.bat** in that folder.

## What gets uploaded
- Every vertical clip in `output\Exports\<date>\`. Highlight compilations and `[horizontal]` versions are skipped.
- The description and hashtags from the clip's `.json` / `.txt`, written by Clip Studio when you export.
- What was sent and when is listed in `output\tiktok\uploaded.json`. Delete a line there to send that clip again.

## Stopping for good
Close the uploader, delete `output\tiktok\`, remove Clip Studio under TikTok → Settings → Security → Manage app permissions, and delete the app in the developer portal.
