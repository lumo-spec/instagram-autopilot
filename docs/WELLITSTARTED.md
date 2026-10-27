# Well It Started — posting videos from a folder on your PC

Everything here runs on your own PC (owner decision, 2026-10-27). Nothing about your videos or
your folders is put in this public repository; the folder path and all keys live in the
git-ignored `.env` file in the project folder.

## How it works
1. You drop a finished video folder into `postthem\`. A folder is ready when it has
   **one `.mp4`, `cover.png` and `publication-copy.txt`** (other files are ignored) and nothing
   in it changed for 10 minutes (so a folder that is still copying is never touched).
2. Videos go out in the order they arrived (the time the folder appeared), each **once per
   destination** (recognised by the video file itself, so the same video under another folder
   name is not posted again).
3. At each slot, the next video is **copied** to `working\`, posted from the copy, and the copy
   is moved to `posted\<name>\` with a file per destination (`Instagram Reels.txt`,
   `YouTube Shorts.txt`, ...: post ID and link) — or to `failed\<name>\` with a
   `... - reason.txt`. Your folder in `postthem\` is never changed or moved.
4. Nothing to post at a slot → the slot is skipped silently. When a destination's videos would
   run out within 3 days you get an e-mail (a GitHub alert, like the Simple Story Mode ones).
5. `PAUSE.txt` in the page folder stops all posting immediately; slots that pass while paused
   are not posted later.

| Destination | When (UK time) | Which videos |
|---|---|---|
| Instagram Reels | daily 07:30, 10:30, 13:00, 17:30, 20:30 | vertical/square, up to 3 min |
| YouTube Shorts | daily 12:00 | the same vertical videos (each video is used once per destination) |
| Long YouTube videos | Tuesday and Saturday 18:00 | landscape, or longer than 3 min |

If the PC is asleep at a slot, Windows wakes it; if it was off, the slot is still posted when it
comes back within 90 minutes, otherwise that slot is skipped (never a burst of posts).

**Failed videos:** read the reason file, fix the folder in `postthem\`, then delete its folder
from `failed\` — it is tried again at the next slot. Temporary problems (network, Meta busy)
are retried at the next two slots first.

## AI label
Both APIs can set the label, so nothing is added to your description:
- Instagram: `is_ai_generated=true` on the reel (Meta's content-publishing guide; works with
  Instagram Login).
- YouTube: `status.containsSyntheticMedia=true` on upload (YouTube Data API, added
  30 Oct 2024).
Switch off with `ai_generated: false` in `pages/wellitstarted/inbox.yaml`.

## Commands (PowerShell, in the project folder)
```powershell
.venv\Scripts\python -m autopilot inbox status wellitstarted       # what is waiting, next slots
.venv\Scripts\python -m autopilot inbox rehearse wellitstarted     # 14 simulated days, fakes only
.venv\Scripts\python -m autopilot inbox rehearse wellitstarted --sample "<folder of video folders>"
.venv\Scripts\python -m autopilot inbox post-now wellitstarted instagram-reels   # ONE video now
.venv\Scripts\python -m autopilot inbox run wellitstarted          # what the scheduled task runs
```

## Setting up (one time)
1. Add the folder to `.env` (one line): `WELLITSTARTED_DIR=<the folder that contains postthem>`.
2. **Instagram, direct upload from this PC** (owner's choice A, 2026-10-27: nothing is put on
   a public web address). Meta only allows uploading a file from a PC with **Facebook Login
   for Business**, so @wellitstarted is connected through a Facebook Page:
   1. @wellitstarted must be a Professional account (Business or Creator) with two-factor
      sign-in, **linked to a Facebook Page** you manage (Instagram app → *Edit profile →
      Page → Connect*, or the Page's *Settings → Linked accounts → Instagram*).
   2. In the same Meta app as Simple Story Mode (developers.facebook.com → *My Apps*), add
      **Facebook Login for Business** (in the Instagram use case: *API setup with Facebook
      login*). Leave the app in Development mode: you are its admin, so no App Review is
      needed to post to your own account.
   3. Open the **Graph API Explorer** (developers.facebook.com/tools/explorer), pick the app,
      *User Token*, tick `instagram_basic`, `instagram_content_publish`, `pages_show_list`,
      `pages_read_engagement` and `business_management`, press *Generate Access Token* and,
      in the Facebook window, choose the Page and @wellitstarted. Copy the token
      (**never paste it into chat**).
   4. In your own PowerShell window:
      `.venv\Scripts\python -m autopilot inbox facebook-login wellitstarted`
      It asks for the app ID (*App settings → Basic*), then the app secret and the token at
      hidden prompts, swaps the token for the Page's own token (Meta: no expiry date, so no
      renewal) and prints the Page and the @handle it found. The app secret is used once and
      not saved; the Page token and account number go into `.env` only.
   If the Page token ever stops working (password change, Page role removed), run step 3-4
   again.
   **Cover:** with direct upload Instagram picks a frame of the video as the cover, because
   Meta takes a custom cover only from a public web address. The result file says so; change
   the cover in the Instagram app if needed. `cover.png` is still used for long YouTube videos.
   Button names above come from Meta's developer docs and were **not checked by hand**.
3. **YouTube** (only when you want Shorts / long videos):
   1. <https://console.cloud.google.com> → new project → *APIs & Services → Library* →
      **YouTube Data API v3** → Enable.
   2. *Google Auth Platform* (OAuth consent screen): External; app name; your e-mail.
      Then **Publish app** (status *In production*): in *Testing* mode Google ends the sign-in
      after 7 days. Google will show "Google hasn't verified this app" when you sign in —
      choose *Advanced → Go to (your app)*: it is your own app.
   3. *Clients → Create client → Desktop app* → keep the client ID and secret.
   4. `.venv\Scripts\python -m autopilot inbox youtube-login wellitstarted` → type the client
      ID, the secret at the hidden prompt, sign in in the browser with the channel's account;
      it prints the channel name so you can check it is the right one.
   5. Custom thumbnails (long videos) need a phone-verified channel
      (<https://www.youtube.com/verify>); without it the video still goes up and the result
      file says the thumbnail was not set.
   6. Set `enabled: true` for `youtube-shorts` / `youtube-long` in
      `pages/wellitstarted/inbox.yaml`.

## Going live (owner, 2026-10-27)
1. Rehearsal passes (`inbox rehearse`), account steps above done.
2. **First live test: one video only** —
   `inbox post-now wellitstarted instagram-reels`. Check the reel, its cover and the
   AI label in the Instagram app.
3. Only after you say **"continue"**: start the schedule with
   `powershell -ExecutionPolicy Bypass -File .\scripts\install-inbox-task.ps1`
   (`-Remove` switches it off again).

## How the video reaches Instagram (decided 2026-10-27: A)
Direct upload: the PC asks Meta for an upload address (`upload_type=resumable`), sends the file
to `rupload.facebook.com` (resuming from where Meta stopped if the connection drops), waits for
processing and publishes. The token goes only to Meta's two hosts, only in a header. Meta's
resumable-upload page (checked 2026-10-27) offers this **only** for Facebook Login for
Business, which is why Instagram Login (Simple Story Mode's connection) is not used here. Never
used for these videos: this public repository or any other public hosting. YouTube uploads
from the PC directly as well.

## YouTube: quota and the private lock (checked 2026-10-27)
- **Quota: fine.** Since 1 June 2026 uploads (`videos.insert`) have their own daily bucket of
  100 uploads, 1 unit each (getting-started and `videos.insert` pages; revision history
  4 Dec 2025 and 1 Jun 2026). Our plan uses 1 Short a day + 2 long videos a week. The other
  calls (thumbnail 50 units, lists and the privacy check about 1 unit each) come from the
  separate 10,000-unit bucket. No quota request is needed.
- **Private lock: unclear, so it is checked on every upload.** Since July 2020, uploads from
  API projects that have not passed YouTube's audit were locked to private. Google's pages now
  disagree: the `videos.insert` reference (updated 15 Oct 2026) says "Videos uploaded from
  unverified API projects are not restricted to private viewing mode", but the revision
  history announces no such change, and the YouTube Help Center still says such videos are
  locked private and cannot be appealed. So after each upload we read the video's privacy
  back. If YouTube shows it as private, the result file says WARNING, you get an e-mail, and
  the video is not uploaded again.
  If that happens: switch `youtube-shorts` / `youtube-long` off in `inbox.yaml`, delete the
  private video in YouTube Studio, and either post YouTube by hand or apply for the free
  YouTube API Services audit (Google's form; it asks for a privacy policy and how the app uses
  the API, and takes weeks). Instagram is not affected.
- The OAuth app must be *In production*: in *Testing* mode Google ends the sign-in after 7 days.

## Notes
- The Toshiba video uses BBC News archive footage (credited in its caption). On YouTube this
  can trigger a Content ID claim (ads to the owner, blocking or muting); Instagram may mute or
  remove it. Your call before it is dropped into `postthem\`.
- YouTube quota and the private lock: see the section above.
