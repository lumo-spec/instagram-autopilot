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
2. **Instagram** (@wellitstarted must be a Professional account with two-factor sign-in):
   in the same Meta app as Simple Story Mode, add @wellitstarted under *Instagram → API setup
   with Instagram login* (add it as an Instagram tester first and accept the invite in the
   Instagram app), press *Generate token*, then in your own PowerShell window:
   `.venv\Scripts\python -m autopilot inbox instagram-token wellitstarted` and paste the token at
   the hidden prompt. It is renewed automatically every 30 days.
   **Still open:** how the video reaches Instagram (see "Video hosting" below).
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

## Video hosting (decision needed before Instagram)
Meta only accepts reels from a **public web address** when the account is connected with
Instagram Login (as Simple Story Mode is): there is no direct upload from a PC for that login
type (Meta's content-publishing guide, checked 2026-10-27). Options:
- **A. Facebook Login for Business** for @wellitstarted (needs a Facebook Page linked to the
  account): the PC uploads the file directly to Meta; nothing public. More Meta setup.
- **B. This repository's media branch** (how Simple Story Mode's slides go out today, proven
  live on 2026-10-20): the video is public on GitHub Pages for a few minutes, then deleted, but
  stays in that branch's history until the weekly clean-up.
- **C. A private cloud bucket** with a short-lived link (e.g. Cloudflare R2): nothing in the
  repository; needs a new account and key.
YouTube needs none of this (the PC uploads directly).

## Notes
- The Toshiba video uses BBC News archive footage (credited in its caption). On YouTube this
  can trigger a Content ID claim (ads to the owner, blocking or muting); Instagram may mute or
  remove it. Your call before it is dropped into `postthem\`.
- YouTube: uploads have their own quota (100 a day per project; checked 2026-10-27) and,
  according to the current `videos.insert` page, uploads from unaudited projects are no longer
  forced to private. **Verify on the first real upload** that it shows as Public.
