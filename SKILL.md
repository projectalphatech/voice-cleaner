---
name: voice-cleaner
description: Clean spoken-word audio Auphonic-style with FFmpeg and numpy - hum removal, denoise, speaker leveler, pause gate, loudness normalization to a LUFS target with a true-peak ceiling - and compare before/after with metrics and spectrograms. Use when the user wants to clean, enhance, level, denoise or normalize a voice recording, podcast, interview, voiceover or reel audio; asks for "Auphonic", "LUFS", "loudness", "hum", "background noise"; or wants to deploy the voice cleaner web app (Cloudflare Worker + VPS job API).
---

# Voice Cleaner

Speech-first audio post-production. Everything runs locally with FFmpeg + numpy. No cloud API, no model download required.

Paths below are relative to this skill's folder.

## Requirements

- `ffmpeg` and `ffprobe` on PATH (FFmpeg 6+).
- Python 3.10+ with `numpy`. The job API also needs `fastapi`, `uvicorn`, `python-multipart` (`scripts/requirements.txt`).
- Optional: DeepFilterNet's `deep-filter` binary on PATH (or `DEEPFILTER_CMD`) for stronger denoising. Without it the chain uses FFmpeg `afftdn`. The DeepFilterNet path is not yet tested.

Check the chain works before using it on real files:

```bash
python scripts/clean.py --selfcheck
```

## Clean one file

```bash
python scripts/clean.py INPUT OUTPUT.mp3 [--target -16] [--no-denoise] [--no-leveler]
```

- Input: any audio or video FFmpeg reads. Output format comes from the extension: `mp3`, `m4a`, `flac`, `wav`.
- `--target` LUFS: `-16` podcast (default), `-14` Spotify/YouTube, `-19` mono podcast, `-23` EBU R128, `-24` ATSC A/85.
- Prints a JSON-like dict: detected hum, speech-to-noise, which denoiser ran, final LUFS and true peak.

## Compare before and after

```bash
python scripts/compare.py INPUT OUTDIR
```

Writes `before.wav`, `after.wav`, `spectrogram.png` (top before, bottom after) and prints a metrics table. Show the user the table and the spectrogram, and tell them to listen to both WAVs. Never claim it sounds better without someone listening.

Reading the metrics:
- `loudness LUFS` should land within 1 LU of the target; `true peak dBTP` at or below -1.0 (-1.5 for lossy).
- `loudness range LU` falling means the leveler worked. Spoken word sits well at 3-6 LU.
- `speech-to-noise dB` rising means pauses stayed quiet while speech was lifted.
- `noise floor dBFS` is the 10th-percentile frame. On phone recordings that gate pauses to digital silence it is misleading; trust speech-to-noise instead.

## The chain (scripts/clean.py)

1. Decode to 48 kHz float, max 2 channels.
2. 80 Hz high-pass (fixed, not adaptive).
3. Hum: finds a 50 or 60 Hz mains line in the spectrum and notches 6 harmonics, 2 Hz wide.
4. Denoise: skipped when speech-to-noise is above 40 dB, since a denoiser can only blur clean speech. Otherwise DeepFilterNet if present, else `afftdn`.
5. Leveler: 400 ms RMS frames; speech rides toward its median level (max ±12 dB, ~300 ms smoothing). Pauses more than 200 ms from speech are gated so that after the later loudness gain they sit near their input level.
6. Two-pass `loudnorm` (linear) to the target, with a true-peak ceiling, then encode.

Tuning knobs, all in `scripts/clean.py`:
- Denoise skip threshold: `snr <= 40` in `clean()`.
- Speech detection: `frame_db()` - 10 dB over the floor and within 30 dB of the loudest speech.
- Gate ramp: the `0.5` passed to `smooth()` for `gate` in `level()`. Lower it (e.g. `0.25`) if pauses pump audibly.
- Word-edge guard: `np.ones(9)` in `level()` (±200 ms).

After any change, run `--selfcheck` and re-run `compare.py` on a real recording.

## Web app (Cloudflare Worker + VPS)

`scripts/server.py` is a token-protected job API (upload, status, download). `worker/` is a Cloudflare Worker that serves the upload page and proxies only `/api/jobs*` to the VPS. Deploy steps and limits: [references/deploy.md](references/deploy.md).

## Known gaps vs Auphonic

- No adaptive high-pass, Voice AutoEQ, de-plosive, bandwidth extension or "Studio Voice" reconstruction.
- No multitrack (crosstalk removal, ducking), no silence/filler/cough cutting, no speech recognition.
- Short pauses (under ~0.5 s) keep speech gain on purpose, to protect word edges.
