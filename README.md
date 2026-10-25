# Voice Cleaner

Speech-first audio cleanup that runs locally with FFmpeg and numpy: hum removal, denoise, speaker leveler with pause gate, and loudness normalization to a LUFS target with a true-peak ceiling. It can also remove ums, ahs and stutters in English and Arabic, using Whisper word timestamps. No cloud API.

It ships as an **agent skill** (`SKILL.md`) that Claude Code, Codex and Cursor can load. It also includes a small web app: a job API for a server, plus a Cloudflare Worker front end.

## Install as a skill

Clone the repo straight into your agent's skills folder, then restart the agent:

```bash
git clone https://github.com/projectalphatech/voice-cleaner ~/.claude/skills/voice-cleaner   # Claude Code
git clone https://github.com/projectalphatech/voice-cleaner ~/.codex/skills/voice-cleaner    # Codex
git clone https://github.com/projectalphatech/voice-cleaner ~/.cursor/skills/voice-cleaner   # Cursor
```

To update, run `git pull` inside the cloned folder.

## Use it directly

Needs `ffmpeg`/`ffprobe` on PATH and Python 3.10+ with `numpy`.

```bash
python scripts/clean.py --selfcheck                    # verify the chain
python scripts/clean.py input.m4a output.mp3 --target -16
python scripts/compare.py input.m4a out/               # metrics table + before/after spectrogram
python scripts/clean.py input.m4a output.mp3 --cut-fillers   # also remove ums, ahs, stutters
```

`--cut-fillers` needs `pip install faster-whisper`. The first run downloads the Whisper model (about 1.6 GB). The output gets a `.cuts.json` listing every cut, so you can review the cuts or reuse them in a video editor.

Loudness targets: `-16` podcast, `-14` Spotify/YouTube, `-19` mono podcast, `-23` EBU R128, `-24` ATSC A/85.

## What the chain does

1. 80 Hz high-pass.
2. Detects 50/60 Hz mains hum and notches its harmonics.
3. Denoises with FFmpeg `afftdn`, or DeepFilterNet if `deep-filter` is installed. This step is skipped when speech is already more than 40 dB above the noise.
4. Levels speakers toward one level and gates pauses so background noise doesn't rise with the gain.
5. Runs a two-pass `loudnorm` to the target LUFS with a true-peak ceiling.

With `--cut-fillers`, fillers and stutters are cut between steps 3 and 4.

Details and tuning settings: [SKILL.md](SKILL.md). Web app deployment: [references/deploy.md](references/deploy.md).
