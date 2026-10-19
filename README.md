# Instagram Autopilot

Makes **Simple Story Mode** (@simplestorymode) posts: illustrated, emotional storybook carousels
and single images. The owner supplies text-free art; the system writes and places every word,
checks each post, and only posts the owner has approved can be exported or published.

> **Current mode: manual posting, moving to automatic (owner decision, 19 Oct 2026).** Nothing
> is posted automatically yet. Until the Instagram connection and the live tests are done
> ("Connecting Instagram" below), you approve a post with `Build`, export it with `Export`, and
> schedule it yourself in Meta Business Suite (guide below).

## Cheat sheet
1. **New post:** in Claude Code type `New post: <topic>`. Claude writes the slides, caption,
   hashtags and an art prompt for each slide.
2. **Make the art** in the locked style (see "Art style" below; every art prompt already starts
   with it), with no text in it. Save it as
   `pages/simplestorymode/queue/<post-id>/art-01.png`, `art-02.png`, ...
3. **Approve:** type `Build <post-id>`. Claude looks at every picture, composes the slides, runs
   every check, and only then approves the post.
4. **Export:** type `Export <post-id>`. You get a folder `exports/<post-id>/` with the finished
   slides in order, `caption.txt` and `NOTES.txt` (including whether to switch on the AI label).
   Story reels are postponed until after the first live post; `Export <post-id> with reel`
   (`--reel`) still makes one (`story-reel.mp4`) to post as a separate reel.
5. **Schedule it** in Meta Business Suite (next section).
6. **Send the batch to GitHub** (keeps the public queue and its backup up to date): one command,
   see "Sending new art to GitHub" below.
7. **Hold one post back:** `Hold <post-id>` in Claude Code (or add the line `hold: true` to its
   `post.yaml`). `Release <post-id>` lets it go again.

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

**Posting the story reel (optional, a separate post):** in Business Suite click **Create reel**
(or **Create post** and add the video). Upload `story-reel.mp4`, choose **Edit cover** → *Upload*
→ `story-reel-cover.jpg`, paste the same `caption.txt`, switch on the AI label if `NOTES.txt`
says YES, and schedule it as above. Post it on a different day from the carousel, or instead
of it. Our music is generated by this program, not by AI, and needs no licence. Don't add
music from Instagram's library on top of it.

**Limits to know:**
| | What Meta says (checked 19 Oct 2026) |
|---|---|
| How far ahead | Instagram app: up to **30 days** ahead (up to a year with a paid *Meta One* plan, which we don't use). Meta's page doesn't give the Business Suite limit; its date picker shows what is allowed. Schedule a week or two ahead and you're safe. |
| Slides per carousel | Our posts have at most 10 slides, which every Meta tool accepts. |
| Picture size | Our slides are 1080 × 1350 (4:5 portrait), a standard feed size. The profile grid shows a slightly narrower 3:4 crop; our text stays clear of those edges. |
| Hashtags | At most 5 per post. `caption.txt` already follows this. |
| AI label | Required for realistic AI video or AI audio (Meta, Feb 2024). Optional for illustrations and for our story reels (moving illustrations, not realistic video), but we switch it on. Meta may add "AI info" automatically when it detects AI content credentials. Instagram's separate *AI-generated profile* label (since Aug 2026) is for profiles featuring an AI-generated person; our characters are clearly illustrated, so we don't use it. |
| Reels | Meta's API specs: MP4, H.264, AAC audio, 23-60 fps, 3 s to 15 min, up to 300 MB, 9:16 recommended. Our story reels are 1080 × 1920, 30 fps, H.264 + AAC 48 kHz, and usually 20-40 s. |

Sources: Meta Help Centre, "Schedule posts and reels further in advance on Instagram"
(meta.com/help/subscriptions/1931535524918664); Meta Business Help Centre, "Schedule posts and
stories on the Business Suite mobile app" (facebook.com/business/help/609176706604372); Meta,
"Labeling AI-Generated Images on Facebook, Instagram and Threads" (about.fb.com, 6 Feb 2024) and
"Our Approach to Labeling AI-Generated Content and Manipulated Media" (about.fb.com, Apr 2024,
updated Sep 2024); Meta for Developers, "IG User Media" reference (reel video specs, checked
19 Oct 2026). The *AI-generated profile* label comes from 2026 news reports, not a Meta page we
could read. Button names come from Meta's help pages and recent guides; they may differ
slightly on your screen.

## Art style (locked)
Every art prompt on the review page starts with this paragraph, and the review page shows it in
its own box at the top. Paste it first when you start a new chat in your image tool:

> Soft watercolour storybook illustration: gentle hand-painted washes, visible paper texture,
> soft edges, loose painterly detail, no hard black outlines. Warm golden autumn light,
> nostalgic and calm. Palette of cream, olive green, rust, gold, warm brown and navy. People
> have natural proportions and normal rounded human ears, never pointed. No text anywhere in
> the picture: no letters, numbers, signs, logos or watermarks.

The prompts never name a studio or an artist. To change the style, ask Claude Code; it lives in
`pages/simplestorymode/characters.yaml` → `style.paragraph`.

## Sending new art to GitHub (one command per batch)
After you have added art and Claude has run `Build` for the posts, open **your own PowerShell
window** and type:
```powershell
cd D:\Projects\instagram-autopilot
powershell -ExecutionPolicy Bypass -File .\scripts\send-posts.ps1
```
That's all. The script brings your computer up to date, runs the privacy check, checks every
post, and sends **only** post files (`post.yaml`, `approved.json`, `art-NN.png/.jpg/.webp`)
as one pull request. GitHub merges it by itself once its checks pass (about 5-15 minutes;
the first time it asks once whether to allow that). Then the script brings your computer up
to date again. Add `-DryRun` to see what it would send without sending anything. If you close
the window early, run it again later to finish. It never asks for a password or token, and
nothing from `references/` can be sent.

## If you change a post's art (or text) later
- **Not yet posted:** `Build <post-id>` records a fingerprint (SHA-256) of the text and every
  picture. Any change, even one pixel, makes the post **"changed since approval"**. It can't be
  exported or published until you run `Build <post-id>` again, which re-checks everything on
  the new art. `Hold`/`Release` don't count as changes.
- **Already exported:** the old `exports/<post-id>/` folder keeps the old pictures. Run
  `Export <post-id>` again after the new Build (it replaces the folder), and delete anything
  already scheduled with the old pictures in Business Suite.
- **Already on Instagram:** nothing here ever edits a published post. Delete it on Instagram
  if you want, then schedule the new export.
- **On GitHub:** send the new art and the new `approved.json` with `send-posts.ps1`.

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
.venv\Scripts\python -m autopilot export simplestorymode <post-id> --reel                # also the story reel (postponed)
.venv\Scripts\python -m autopilot export simplestorymode <post-id> --reel --music quiet-piano.flac   # with a CC0 track
.venv\Scripts\python -m autopilot hold simplestorymode <post-id> "reason"                # never posted while held
.venv\Scripts\python -m autopilot release simplestorymode <post-id>
.venv\Scripts\python -m autopilot rehearse --days 14                 # practise two weeks of posting (never posts)
```
`preview` and `build` name an `index.html` file; open it in a browser. A story reel takes about
a minute to make. CC0 music tracks go in `assets/music/` (see its README).

## Changing things
- **Suggested posting times and days:** `config.yaml` → `pages.simplestorymode.slots`.
- **Adding a page:** each new page needs its own plan and approval first (see the CLAUDE.md
  roadmap).

## Connecting Instagram (automatic posting; set up in progress since 19 Oct 2026)
You asked on 19 Oct 2026 to go back to automatic posting, first live post as fast as possible.
The automatic carousel path (build → host → publish → record, started with **Post now**) is
finished and tested against a fake Instagram; it has never talked to the real one. Until the
first live post works you can still post by hand (`Build` + `Export`, guide above).

**Minimum for the first live post** (everything else can wait):
1. c01 art → `Build c01-you-can-miss` → `send-posts.ps1` (the art and its approval must be on
   GitHub, because the robot builds the post there).
2. Meta app + Instagram tester + token (steps A-F below), stored with `set-token.ps1`.
3. Tell Claude Code **"token set"**: it switches on the safety switch `PUBLISHING_ENABLED`.
4. Run **Post now** with `c01-you-can-miss` (step G).

**Can wait until after the first post:** the renewal key (step H; needed within about 6 weeks,
because the token lasts 60 days and can only be renewed once it is a day old), the image and
reel live tests, the renewal test, egress block mode, and the weekly schedule. The schedule
stays off until all of those pass.

Checked against Meta's developer docs on 19 Oct 2026. Meta renames buttons often, so if one is
missing, look for the closest match and tell Claude Code what you see.

- **A. Account check.** On your phone, @simplestorymode must be a Professional account (done)
  and **public**: Instagram app → profile → ☰ → **Account privacy** → *Private account* off.
- **B. Meta developer account.** Go to <https://developers.facebook.com/>, log in with your
  Facebook account and click **Get started** if you have never used it. Confirm your
  e-mail/phone if asked.
- **C. Create the app.** <https://developers.facebook.com/apps> → **Create App** (top right; the
  first time **Create First App**).
  1. *Connect a business:* choose the option to **not** connect a business portfolio now →
     **Next**. (It can be added later; it is not needed to post to your own account.)
  2. *Use case:* **Other** → **Next**.
  3. *App type:* **Business** → **Next**.
  4. *Details:* app name `simplestorymode-autopilot`, your contact e-mail → **Next** / **Create
     app**. Enter your Facebook password if asked.

  Leave the app in *Development* mode. App Review is only for other people's accounts; your own
  account works as a tester (to be confirmed by the first live post).
- **D. Add Instagram.** In the app dashboard scroll to **Instagram** → **Set up**. The left
  menu now shows **Instagram → API setup with Instagram login**.
- **E. Make @simplestorymode a tester.** Left menu **App roles → Roles** → **Add people** →
  **Instagram Tester** → type `simplestorymode` → **Add**. Then accept the invite as
  @simplestorymode: on a computer open <https://www.instagram.com/accounts/manage_access/>
  → **Tester invites** → **Accept** (in the app: Settings → *Website permissions* → *Apps and
  websites* → *Tester invites*). Skip this step if the next one adds the account directly.
- **F. Generate and store the token.** Left menu **Instagram → API setup with Instagram login**
  → section **1. Generate access tokens** → **Add account** → log in as @simplestorymode →
  allow the permissions (at least *basic* and *content publish*). Back on the page, click
  **Generate token** next to simplestorymode, tick "I understand", **Copy**. Do not paste it
  anywhere else. Straight away, in **your own** PowerShell window:
  ```powershell
  cd D:\Projects\instagram-autopilot
  powershell -ExecutionPolicy Bypass -File .\scripts\set-token.ps1
  ```
  Paste at the hidden prompt (only stars show) → Enter. It shows the account name; it must say
  `@simplestorymode` → type `yes`. It stores the token and the account ID encrypted in GitHub
  and records today's date (the token lasts 60 days). Then tell Claude Code **"token set"**.
- **G. First live post: Post now.** On <https://github.com/lumo-spec/instagram-autopilot> →
  **Actions** → left list **Post now** → **Run workflow** (right) → *Branch* `main`, *Page*
  `simplestorymode`, *Post id* `c01-you-can-miss`, *Kind* `auto` → green **Run workflow**. It
  takes about 5-10 minutes. Green ticks on *build*, *host*, *publish*, *record* = posted; the
  status page shows the link. If anything goes red, tell Claude Code; nothing is posted twice.
  (Phone: GitHub app → the repository → Actions → Post now → Run workflow.)
- **H. Renewal key (later, within about 6 weeks).**
  <https://github.com/settings/personal-access-tokens/new>: name `ig-token-renewal`, expiration
  1 year, repository access *Only select repositories* → `lumo-spec/instagram-autopilot`,
  permission **Environments: Read and write** (nothing else) → **Generate token**, copy, then
  in your own PowerShell window run
  `powershell -ExecutionPolicy Bypass -File .\scripts\set-renewal-key.ps1`.
- **I. Before the schedule goes on:** live tests for one single image and one reel via Post now,
  one token renewal (needs H), egress block mode. Only then is the weekly schedule switched on.

Never paste a token or key into a chat, an issue or a file.

The status page ([STATUS.md on the state branch](https://github.com/lumo-spec/instagram-autopilot/blob/state/STATUS.md))
and the alert e-mails (GitHub issues labelled `autopilot-alert`) belong to the automatic system.

See `CLAUDE.md` for technical notes and procedures, and `SECURITY.md` for the security design.
