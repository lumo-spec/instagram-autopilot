# Music for story reels (optional)

By default, story reels use **our own generated music**: a soft pad plus piano-like notes, made
fresh for every reel by `src/autopilot/audio/music.py`. It needs no licence and no file here.

If you would rather use a **real recorded piano or flute** track, it must be **CC0 1.0** (public
domain dedication). Attribution-only licences such as CC BY are not accepted, and neither are
"free to use" site licences, because their terms can change and are hard to prove later.

## Adding a CC0 track
1. Download the track from a page that clearly states **CC0** for that exact track. Save that
   page's address.
2. Put the file in this folder (`.wav`, `.flac`, `.ogg` or `.mp3`). Use a short name with no
   spaces, e.g. `quiet-piano.flac`.
3. Ask Claude Code to "add the music licence record for quiet-piano.flac". It adds an entry like
   this to `licences.json`, with the file's SHA-256 so that a swapped file is refused:
   ```json
   {
     "quiet-piano.flac": {
       "title": "Quiet Piano",
       "creator": "Name of the musician",
       "source_url": "https://example.org/the-track-page",
       "licence": "CC0-1.0",
       "sha256": "…",
       "added_on": "2026-10-19"
     }
   }
   ```
4. Use it for one export with
   `python -m autopilot export simplestorymode <post-id> --music quiet-piano.flac`,
   or for every story reel by setting `story_reel.music: quiet-piano.flac` in `config.yaml`.

A track without a complete record, with a licence other than CC0-1.0, or whose file no longer
matches its SHA-256 is refused, and the export says why. The repository is public, so only add
tracks you are happy to publish here (CC0 allows it).
