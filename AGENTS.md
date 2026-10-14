# vidgrab — instructions for agents

You can download videos for your owner with this tool. It resolves a TikTok, YouTube, Douyin or X link
through Apify, downloads the video, and prints **one line of JSON** on stdout.

## Setup (once)

```bash
git clone https://github.com/jiebaosystem/vidgrab.git && cd vidgrab
cp .env.example .env   # then put the owner's Apify token after APIFY_TOKEN=
python3 vidgrab.py --version
```

- Needs only `python3` (3.8+). No packages to install.
- Install `ffmpeg` if you can (`brew install ffmpeg` / `apt install ffmpeg`). Without it, YouTube videos
  come in lower quality (YouTube serves HD picture and sound as separate files that must be merged).
- Never print, log or commit the token. `.env` is git-ignored.
- To update later: `git pull`.

## When the owner sends a video link

1. Pass the owner's message **as is**, quoted as a single argument. Share texts with extra words are fine;
   the first link is used.
   ```bash
   python3 /path/to/vidgrab/vidgrab.py "<the owner's message>"
   ```
2. Read the single JSON line from stdout.
3. If `"ok": true`, send the owner, in one message:
   - the **video file** at `file`,
   - the **playable link** `play_url`, saying it usually expires within a few hours,
   - the `title` and the original `source_url`,
   - `note`, if present (for example: lower quality because ffmpeg is missing).
   If `audio_url` is present, `play_url` is picture only; mention that the file you send has sound.
4. If `"ok": false`, tell the owner which step failed (`stage`) and the `error`. Do not retry more than once.
   - `resolve`: the video may be deleted, private or temporarily blocked. Ask for another link.
   - `download`: retry once, because the link may have expired; if it fails again, send `play_url` if there is one.
   - `config`: the Apify token is missing. Ask the owner for it.

## Things to know

- Takes about 5–40 seconds, depending on video size. Run it with a timeout of a few minutes.
- One link per call. Don't loop over many links; batch jobs are out of scope.
- Files go to `~/vidgrab` (or `$VIDGRAB_DIR`). The tool deletes its own files older than 7 days on every run,
  so you don't need to clean up. Don't store anything else you need in that folder under a `vidgrab-` name.
- `python3 vidgrab.py resolve "<link>"` only resolves the link and gets `play_url`, without downloading.
- Output fields, error stages and settings are documented in [README.md](README.md).
