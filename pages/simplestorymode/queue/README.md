# Simple Story Mode — post queue

Each folder here is one post. Only posts with a valid `approved.json` (written by
`Build <post-id>` after every gate passed) are ever published, in order of `priority`
(lower first), then approval time. If no approved post fits a slot, the slot is skipped and an
alert is raised — nothing else is ever posted.

```
queue/<post-id>/
  post.yaml        slide text, art briefs, caption, hashtags  (written by Claude: "New post: <topic>")
  art-01.png ...   the owner's text-free art, one per slide   (4:5 for posts, 9:16 for reels)
  approved.json    written by "Build <post-id>" (SHA-256 of post.yaml and every art file)
  preview/         local preview of the composed slides (git-ignored)
```

Changing `post.yaml` or any art file after approval holds the post back until it is built again.

## post.yaml

```yaml
id: c01-you-can-miss            # must equal the folder name (lowercase, digits, hyphens)
format: carousel                # carousel (2-10 slides) | image (1 slide) | reel (1 slide)
kind: repeated-opening          # story | one-feeling | repeated-opening | single | reel
topic: missing someone you were right to leave
priority: 100                   # optional; lower is posted sooner
slides:
  - text: |                     # line break = new line; blank line = new paragraph
      You can miss someone
      and still know **leaving was right.**     # **...** = accent colour
    position: top               # where the art leaves quiet space: top | middle | bottom
    focus: center               # how to crop the art: top | center | bottom
    brief:                      # turned into a copy-paste art prompt in the preview page
      scene: ...
      characters: [wren]        # rowan | wren | others | none
      emotion: ...
      composition: ...          # say where the quiet space is
caption: |
  Words under the post (no hashtags here).
hashtags: ["#simplestorymode", "#healing", "#feelings"]   # 3-5
audio: voice+music              # reels only: voice+music | voice | music
```

Rules the gates enforce: slide text is at least 60 px with contrast of at least 7:1; every slide
after the first uses the same size and the first is the same size or larger; the first slide has
16 words or fewer; text stays inside the safe zone; spelling; caption length; 3-5 hashtags; and art
is not enlarged more than 1.6x.
