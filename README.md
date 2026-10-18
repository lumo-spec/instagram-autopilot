# Instagram Autopilot

Calm "daily wisdom" posts for Instagram — one quote image and one short animated reel a day —
made and published automatically with the official Instagram API.

> **Status: in development.** Nothing is posted yet. Automatic posting stays switched off until
> the owner has approved the samples and the live tests have passed. The owner's cheat sheet
> (post now, pause, top up content, renew keys) will appear here once posting is set up.

## This repository is public
Everything here — code, content, logs, alerts and status pages — is public. **No password,
token or private ID is ever stored in this repository or its logs.** Secrets live only in
encrypted GitHub Environment secrets that the owner types in at a hidden prompt.

## What it makes
- **Images:** 1080×1350 quote cards in eight rotating styles, with open-licence fonts.
- **Reels:** 1080×1920, 10–30 seconds, text revealed word by word in time with a calm,
  locally generated British voice ([Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), Apache-2.0)
  and/or freshly generated ambient music (no copyrighted music).
- **Content:** 448 entries — original short reflections and public-domain quotes checked word for
  word against Project Gutenberg texts.
- **Quality gates:** every post is checked for readable size, contrast against the actual pixels,
  safe-zone placement, spelling, caption limits, audio loudness, voice timing and file specs.
  A failed check falls back to a safer format; nothing flawed is posted.

## Try the practice run (no Instagram needed)
On Windows, from this folder, after `uv sync`:
```powershell
.venv\Scripts\python -m autopilot rehearse --days 14
.venv\Scripts\python -m autopilot sample wisdom
```
Then open `rehearsal-output\...\index.html` in a browser to see every simulated post.

## Adding content
Open Claude Code in this folder and type **Top up Wisdom**. It writes and checks new entries,
removes near-duplicates and tells you how much stock is left.

See `CLAUDE.md` for technical notes and `SECURITY.md` for the security design.
