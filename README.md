# Instagram Autopilot

Publishes **Simple Story Mode** (@simplestorymode): illustrated, emotional storybook carousels
and single images, with the official Instagram API. The owner supplies text-free art; the system
writes and places every word, checks each post, and posts only what the owner has approved.

> **Status: in development.** Nothing is posted yet. Automatic posting stays switched off until
> the owner has approved the samples and the live tests have passed.

## This repository is public
Everything here — code, post scripts, art in the queue, logs, alerts and status pages — is
public. **No password, token or private ID is ever stored in this repository or its logs.**
Secrets live only in encrypted GitHub Environment secrets that the owner types in at a hidden
prompt. Queued posts (text and art) are visible here before they are published.
The owner's private reference photos stay on their PC (`references/` is never committed).

## How a post is made
1. In Claude Code, type **New post: &lt;topic&gt;**. Claude writes the slide text, caption,
   hashtags and an art brief (with a ready-to-copy art prompt) for every slide.
2. Make the art in your image tool — no text in the art — and save it as
   `pages/simplestorymode/queue/<post-id>/art-01.png`, `art-02.png`, ...
3. Type **Build &lt;post-id&gt;**. The slides are composed, every check runs, and the post joins
   the approved queue.
4. At each scheduled slot the next approved post is published. If none is ready the slot is
   skipped and you get an alert — nothing else is ever posted.

## What the checks cover
Huge, high-contrast text (at least 60 px, 7:1 contrast measured on the real pixels, a soft cream
panel added only where the art is busy), safe zones, spelling, caption length, 3–5 hashtags,
unique captions, carousel rules (2–10 slides, one text size on every slide, a short, strong first
slide), art resolution, and files unchanged since approval.

## Try it without Instagram
On Windows, from this folder, after `uv sync`:
```powershell
.venv\Scripts\python -m autopilot queue                       # what each post still needs
.venv\Scripts\python -m autopilot preview simplestorymode     # render every post (placeholder art)
.venv\Scripts\python -m autopilot rehearse --days 14          # practise two weeks of posting
```
Then open the `index.html` file it names in a browser.

See `CLAUDE.md` for technical notes and procedures, and `SECURITY.md` for the security design.
