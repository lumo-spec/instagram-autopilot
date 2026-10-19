# Instagram Autopilot

Makes **Simple Story Mode** (@simplestorymode) posts: illustrated, emotional storybook carousels
and single images. The owner supplies text-free art; the system writes and places every word,
checks each post, and only posts the owner has approved can be exported or published.

> **Current mode: manual posting (owner decision, 19 Oct 2026).** Nothing is posted
> automatically. There is no Meta developer app and no Instagram token. You approve a post with
> `Build`, export it with `Export`, and schedule it yourself in Meta Business Suite (guide
> below). The automatic-posting code stays in the repository, switched off, for later.

## Cheat sheet
1. **New post:** in Claude Code type `New post: <topic>`. Claude writes the slides, caption,
   hashtags and an art prompt for each slide.
2. **Add the art** (no text in it) as `pages/simplestorymode/queue/<post-id>/art-01.png`,
   `art-02.png`, ...
3. **Approve:** type `Build <post-id>`. Claude looks at every picture, composes the slides, runs
   every check, and only then approves the post.
4. **Export:** type `Export <post-id>`. You get a folder `exports/<post-id>/` with the finished
   slides in order, `caption.txt` and `NOTES.txt` (including whether to switch on the AI label).
5. **Schedule it** in Meta Business Suite (next section).

## How to schedule in Meta Business Suite
Checked against Meta's help pages on 19 October 2026. Meta changes button names often, so if a
button below looks different, look for the nearest match. Scheduling in Meta Business Suite is
free.

**Before the first time (once):**
- Your Instagram account must be a **Professional account** (Creator or Business). In the
  Instagram app: Profile → ☰ menu → Settings → *Account type and tools* → *Switch to
  professional account*. Turn on two-factor authentication while you are in Settings.
- Open <https://business.facebook.com> and log in. If Meta Business Suite asks you to connect
  your Instagram account (it may also ask for a Facebook Page), follow its steps. You only need
  to do this once.

**For each post (on a computer):**
1. In Claude Code type `Export <post-id>`. Open the folder `exports\<post-id>\` and read
   `NOTES.txt`. It lists the files in order, the suggested day and time, and whether the AI
   label is needed.
2. In Meta Business Suite open **Content** (sometimes called **Planner**) and click
   **Create post**.
3. Under **Post to**, tick **Instagram** only. Untick Facebook unless you want it there too.
4. Click **Add photo/video** → *Upload from desktop*. Select the slides in order: `01.jpg`, then
   `02.jpg`, and so on. Check that the thumbnails are in the same order and drag any that are
   wrong. **`01.jpg` must be first: it is the cover.**
5. Open `caption.txt` in Notepad, press **Ctrl+A** then **Ctrl+C**, and paste it into the text
   box. It already ends with the hashtags, so don't add more: Instagram allows at most 5.
6. **AI label:** if `NOTES.txt` says *AI LABEL: YES*, switch on the AI label setting (Instagram
   shows it as **"AI info"**). It is usually a switch called *Add AI label*, on the posting screen
   or under *Advanced settings*. If you can't find it in Business Suite, schedule that post from
   the Instagram app instead (below), where the switch is shown when you share. Meta *requires*
   the label for realistic AI video or AI audio. For still illustrations it is not required, but
   we switch it on to be open about it. The caption already says "Illustrations: AI-generated",
   and Instagram may add the label by itself because our art carries AI content credentials.
7. Click the arrow next to **Publish** (or the **Schedule** option), choose **Schedule**, and
   pick the date and time from `NOTES.txt`. That time is **UK time**; Business Suite shows your
   computer's time zone, so convert if you are elsewhere. Click **Schedule**.
8. Open **Content → Calendar/Planner** and check that the post shows at the right time. You can
   change or delete it there until it goes out.
9. After it goes live, open it on Instagram and check the slide order, caption and label. Then
   rename the export folder (for example `c01-you-can-miss - scheduled 24 Oct`) so you never
   post it twice.

**On a phone:** in the **Meta Business Suite** app, tap **Create** → *Post*, choose Instagram,
add the slides in order, paste the caption, then choose **Schedule for later** and set the time.
In the **Instagram app**, add the slides and caption, open **Advanced settings** (or **More
options**), switch on **Schedule this post**, and pick the time. The AI label switch is on
that screen too.

**Limits to know:**
| | What Meta says (checked 19 Oct 2026) |
|---|---|
| How far ahead | Instagram app: up to **30 days** ahead (up to a year with a paid *Meta One* plan, which we don't use). Meta's page doesn't give the Business Suite limit; its date picker shows what is allowed. Schedule a week or two ahead and you're safe. |
| Slides per carousel | Our posts have at most 10 slides, which every Meta tool accepts. |
| Picture size | Our slides are 1080 × 1350 (4:5 portrait), a standard feed size. The profile grid shows a slightly narrower 3:4 crop; our text stays clear of those edges. |
| Hashtags | At most 5 per post. `caption.txt` already follows this. |
| AI label | Required for realistic AI video or AI audio (Meta, Feb 2024). Optional for illustrations. Meta may add "AI info" automatically when it detects AI content credentials. |

Sources: Meta Help Centre, "Schedule posts and reels further in advance on Instagram"
(meta.com/help/subscriptions/1931535524918664); Meta Business Help Centre, "Schedule posts and
stories on the Business Suite mobile app" (facebook.com/business/help/609176706604372); Meta,
"Labeling AI-Generated Images on Facebook, Instagram and Threads" (about.fb.com, 6 Feb 2024) and
"Our Approach to Labeling AI-Generated Content and Manipulated Media" (about.fb.com, Apr 2024,
updated Sep 2024). Button names come from Meta's help pages and recent guides; they may differ
slightly on your screen.

## This repository is public
Everything here, including code, post scripts, art in the queue and logs, is public. **No
password, token or private ID is ever stored in this repository.** Queued posts (text and art)
are visible here before they are posted. Exported folders (`exports/`) stay on your PC and are
never committed. The owner's private reference photos stay on their PC too (`references/` is
never committed).

## Try it on your PC
On Windows, from this folder, after `uv sync`:
```powershell
.venv\Scripts\python -m autopilot queue                              # what each post still needs
.venv\Scripts\python -m autopilot preview simplestorymode            # render every post (placeholder art)
.venv\Scripts\python -m autopilot build simplestorymode <post-id>    # approve a post with its real art
.venv\Scripts\python -m autopilot export simplestorymode <post-id>   # finished files for Business Suite
.venv\Scripts\python -m autopilot rehearse --days 14                 # practise two weeks of posting (never posts)
```
`preview` and `build` name an `index.html` file; open it in a browser.

## Changing things
- **Suggested posting times and days:** `config.yaml` → `pages.simplestorymode.slots`.
- **Adding a page:** each new page needs its own plan and approval first (see the CLAUDE.md
  roadmap).

## Automatic posting (paused, kept for later)
The GitHub Actions workflows, media hosting, alerts, status page and token renewal from stage 2
are still here and tested, but **switched off**. There are no schedules, no Meta app, no Instagram
token, and the repository switch `PUBLISHING_ENABLED` is not set. If you decide to use them
later, the setup would be:
1. A Meta developer app with **Instagram Login**, your account added as a tester, and a
   long-lived token.
2. A fine-grained GitHub token for this repository only (Environments: read and write; 1 year),
   entered with `.\scripts\set-renewal-key.ps1` in **your own PowerShell window**.
3. The Instagram token, entered with `.\scripts\set-token.ps1` in **your own PowerShell window**.
   Never paste a secret into a chat.
4. Live tests (one image, one carousel, one reel, one token renewal), then the schedule.

The status page ([STATUS.md on the state branch](https://github.com/lumo-spec/instagram-autopilot/blob/state/STATUS.md))
and the alert e-mails (GitHub issues labelled `autopilot-alert`) belong to this paused system.

See `CLAUDE.md` for technical notes and procedures, and `SECURITY.md` for the security design.
