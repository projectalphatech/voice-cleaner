# Voice Cleaner

Speech-first audio cleanup that runs locally with FFmpeg and numpy: hum removal, denoise, speaker leveler with pause gate, and loudness normalization to a LUFS target with a true-peak ceiling. No cloud API.

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
```

Loudness targets: `-16` podcast, `-14` Spotify/YouTube, `-19` mono podcast, `-23` EBU R128, `-24` ATSC A/85.

## What the chain does

1. 80 Hz high-pass.
2. Detects 50/60 Hz mains hum and notches its harmonics.
3. Denoises with FFmpeg `afftdn`, or DeepFilterNet if `deep-filter` is installed. This step is skipped when speech is already more than 40 dB above the noise.
4. Levels speakers toward one level and gates pauses so background noise doesn't rise with the gain.
5. Runs a two-pass `loudnorm` to the target LUFS with a true-peak ceiling.

Details and tuning settings: [SKILL.md](SKILL.md). Web app deployment: [references/deploy.md](references/deploy.md).
