# Instagram Autopilot

Publishes **Simple Story Mode** (@simplestorymode): illustrated, emotional storybook carousels
and single images, with the official Instagram API. The owner supplies text-free art; the system
writes and places every word, checks each post, and posts only approved queue items.

> **Status: in development.** Nothing is posted yet. Automatic posting stays switched off until
> setup and the live tests are complete. **Status page:**
> [STATUS.md on the state branch](https://github.com/lumo-spec/instagram-autopilot/blob/state/STATUS.md)

## Cheat sheet (most of it works from the GitHub phone app)
1. **Post something now:** GitHub app → this repo → Actions → **Post now** → Run workflow →
   leave "Post id" empty for the next approved post (or type one, e.g. `c01-you-can-miss`).
2. **Check everything is healthy:** open the Status page (link above).
3. **Pause or resume automatic posting:** Actions → **Autopilot** → "…" → Disable / Enable workflow.
4. **Add more posts:** when the "approved posts left" e-mail arrives, open Claude Code in this
   folder and type `New post: <topic>`, add the art, then `Build <post-id>`.
5. **Yearly:** renew the renewal key when its reminder e-mail arrives (step 5 below).
6. **If a token renewal fails:** make a fresh Instagram token and run step 6 below.

If a **Post now** run shows as *cancelled*, just run it again (only one posting run can wait at
a time; scheduled slots catch up by themselves).

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
   the approved queue. Any later change to its text or art holds it back until it is built again.
4. At each scheduled slot the next approved post is published. If none is ready the slot is
   skipped and you get an alert — nothing else is ever posted.

## Alert e-mails (GitHub issues labelled `autopilot-alert`)
| Title starts with | Meaning | What to do |
|---|---|---|
| `ACTION NEEDED [Simple Story Mode]: only N approved posts left` | Queue running low | `New post: <topic>` in Claude Code, add art, `Build` |
| `ACTION NEEDED [...]: no approved post for the … slot` | A slot was skipped | Same as above |
| `[...] post X changed after approval` | Text/art edited after Build | `Build X` again |
| `[...] a post could not be published` | Instagram refused it | Nothing if it is retried; if it repeats, ask Claude Code |
| `[...] media hosting failed` | Images/video not reachable | Retried automatically; if repeated, ask Claude Code |
| `[...] token renewal failed` | Weekly renewal failed; old token kept | If repeated, step 6 |
| `ACTION NEEDED [...]: Instagram token expires in N days` / `token not working` | Token problem | Step 6 |
| `ACTION NEEDED: renewal key expires …` | Yearly key renewal | Step 5 |
| `[...] N scheduled posts were missed` | Schedule not running | Check Actions → Autopilot is enabled |
| `Instagram API version retires on …` | Upgrade due | Ask Claude Code to upgrade |

Alerts close themselves when the problem clears.

## Owner setup steps (Claude Code guides you click by click)
1. Instagram **Professional** account (Creator or Business) with two-factor authentication.
2. GitHub account with two-factor authentication, GitHub CLI logged in, GitHub mobile app.
3. Meta developer app with **Instagram Login**; add the account as a tester; create a
   long-lived token.
4. A fine-grained GitHub token for this repository only (Environments: read and write; 1 year).
5. In **your own PowerShell window**: `.\scripts\set-renewal-key.ps1` (renewal key).
6. In **your own PowerShell window**: `.\scripts\set-token.ps1` (Instagram token and user id).
   Never paste a secret into a chat.

## Changing things
- **Posting times and days:** `config.yaml` → `pages.simplestorymode.slots`.
- **Voice (reels):** `config.yaml` → `audio.voices`.
- **Adding a page:** each new page needs its own plan and approval first (see CLAUDE.md roadmap).
- GitHub switches off scheduled workflows after 60 days without repository activity; the weekly
  maintenance run keeps them on.

## Try it without Instagram
On Windows, from this folder, after `uv sync`:
```powershell
.venv\Scripts\python -m autopilot queue                       # what each post still needs
.venv\Scripts\python -m autopilot preview simplestorymode     # render every post (placeholder art)
.venv\Scripts\python -m autopilot rehearse --days 14          # practise two weeks of posting
```
Then open the `index.html` file it names in a browser.

See `CLAUDE.md` for technical notes and procedures, and `SECURITY.md` for the security design.
