# Upload exports to TikTok automatically

**TikTok Uploader.bat** watches `output\Exports\` and uploads each new vertical clip to your TikTok with its caption and hashtags (from the clip's `.txt`). It remembers what it has uploaded, so nothing goes up twice.

TikTok only lets registered apps upload, so you need your own free TikTok developer app. It's a one-time setup of about 15 minutes.

## 1. Turn on the sign-in pages (one click on GitHub)
TikTok needs a privacy policy, terms of service, and a web page to return to after sign-in. They're already in this project; you just publish them:

1. On github.com, open your **clip-studio** repository, then **Settings** → **Pages**.
2. Under *Build and deployment*, set Source to **Deploy from a branch**, Branch to **main**, folder **/docs**. Click **Save**.
3. After a minute these work:
   - `https://lcoutras.github.io/clip-studio/privacy.html`
   - `https://lcoutras.github.io/clip-studio/terms.html`
   - `https://lcoutras.github.io/clip-studio/tiktok-callback.html`

## 2. Create the TikTok app
1. Go to **developers.tiktok.com**, log in with your TikTok account, and accept the developer terms.
2. **Manage apps** → **Connect an app**. Name it "Clip Studio", and choose platform **Web**.
3. App details:
   - Terms of Service URL and Privacy Policy URL: the two links above.
   - Website / URL verification, if asked: `https://lcoutras.github.io/clip-studio/`.
4. **Add products:**
   - **Login Kit**. Redirect URI: `https://lcoutras.github.io/clip-studio/tiktok-callback.html`
   - **Content Posting API**. Turn on **Upload**; turn on **Direct Post** too if you'll use direct mode.
5. **Scopes:** `user.info.basic`, `video.upload` (plus `video.publish` for direct mode).
6. **Sandbox:** add your own TikTok account as a target user, so you can use it right away.
7. Copy the **Client key** and **Client secret**.

## 3. Tell Clip Studio
Add this to `settings.yaml` in the Clip Studio folder:
```yaml
tiktok:
  client_key: "your client key"
  client_secret: "your client secret"
  mode: drafts          # drafts or direct (see below)
  max_per_day: 5        # TikTok limits how many posts an account can make per day
```
Keep the client secret private: don't post it or send it to anyone. It stays on your PC.

## 4. Run it
Double-click **TikTok Uploader.bat**. The first time, your browser opens TikTok: sign in and allow access. After that it uploads new exports and checks again every 10 minutes. Leave the window open; close it to stop.

To start it with Windows: press Win+R, type `shell:startup`, and put a shortcut to **TikTok Uploader.bat** in that folder.

## drafts or direct?
- **drafts (default):** each clip lands in your **TikTok inbox**. Tap the notification and press **Post** (about 10 seconds). Clips can be public right away, and you can add a trending sound first.
- **direct:** posts straight to your profile. Until TikTok **reviews (audits) your app**, these posts are **private**, and your account has to be set to private while posting. To post publicly with no tap, submit the app for review in the developer portal. TikTok's rules require the app to show you each post and get your OK before it publishes, so review may require a confirm step anyway.

## What gets uploaded
- Every vertical clip in `output\Exports\<date>\`. Highlight compilations and `[horizontal]` versions are skipped.
- The description is the clip's caption and title plus its hashtags. `#shorts` is swapped for `#fyp`; change the extra tags with `extra_tags: ["#fyp", "#rocketleagueclips"]`.
- What was uploaded and when is listed in `output\tiktok\uploaded.json`. Delete a line there to upload that clip again.
