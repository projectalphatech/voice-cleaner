---
name: voice-cleaner
description: Clean spoken-word audio Auphonic-style with FFmpeg and numpy - hum removal, denoise, speaker leveler, pause gate, loudness normalization to a LUFS target with a true-peak ceiling - plus optional removal of filler words (um, uh, ممم, آآ) and stutters via Whisper word timestamps, and before/after comparison with metrics and spectrograms. Use when the user wants to clean, enhance, level, denoise or normalize a voice recording, podcast, interview, voiceover or reel audio; remove ums, ahs, filler words or stutters; asks for "Auphonic", "LUFS", "loudness", "hum", "background noise"; or wants to deploy the voice cleaner web app (Cloudflare Worker + VPS job API).
---

# Voice Cleaner

Speech-first audio post-production. Everything runs locally with FFmpeg + numpy, no cloud API. Only filler/stutter removal needs a model (Whisper, downloaded once).

Paths below are relative to this skill's folder.

## Requirements

- `ffmpeg` and `ffprobe` on PATH (FFmpeg 6+).
- Python 3.10+ with `numpy`. The job API also needs `fastapi`, `uvicorn`, `python-multipart` (`scripts/requirements.txt`).
- Optional: DeepFilterNet's `deep-filter` binary on PATH (or `DEEPFILTER_CMD`) for stronger denoising. Without it the chain uses FFmpeg `afftdn`. The DeepFilterNet path is not yet tested.
- Optional, for `--cut-fillers`: `pip install faster-whisper`. The first run downloads the model (`large-v3-turbo`, about 1.6 GB, cached). On CPU, expect roughly real time or slower; set `WHISPER_MODEL=small` for speed at lower accuracy.

Check the chain works before using it on real files:

```bash
python scripts/clean.py --selfcheck
python scripts/disfluency.py --selfcheck   # filler/stutter rules, no model needed
```

## Clean one file

```bash
python scripts/clean.py INPUT OUTPUT.mp3 [--target -16] [--no-denoise] [--no-leveler]
```

- Input: any audio or video FFmpeg reads. Output format comes from the extension: `mp3`, `m4a`, `flac`, `wav`.
- `--target` LUFS: `-16` podcast (default), `-14` Spotify/YouTube, `-19` mono podcast, `-23` EBU R128, `-24` ATSC A/85.
- Prints a JSON-like dict: detected hum, speech-to-noise, which denoiser ran, final LUFS and true peak.

## Remove ums, ahs and stutters

```bash
python scripts/clean.py INPUT OUTPUT.mp3 --cut-fillers [--cut-untranscribed]
python scripts/disfluency.py INPUT          # dry run: print what would be cut
```

Whisper (`faster-whisper`, word timestamps, a verbatim prompt that shows fillers) transcribes the audio. `scripts/disfluency.py` then decides what to cut:
- **Fillers:** English um, uh, er, erm, ah, hmm, mm. Arabic tokens made only of alef/hamza (آآ, إأ) or only meem (مم, ممم). Kept on purpose because they carry meaning: آه ("yes"), أم, إيه ("what"), يعني.
- **Stutters:** a short word (3 letters or fewer) repeated back to back ("the the", "أنا أنا"), or a fragment before the full word ("I- I", "b- but", "ب بس"). Longer repeats like "very very" or "واحدة واحدة" are kept as intentional emphasis.
- **Untranscribed hesitations:** a 0.2–1.2 s gap between words that still has voice in it, which is usually a filler Whisper skipped. These are listed but cut only with `--cut-untranscribed`, because Whisper sometimes skips real words too.

Cuts are padded 20 ms (never into a neighbouring word) and joined with a 10 ms equal-power crossfade. Cuts happen after denoise and before the leveler, so loudness is still measured on the final audio. A `<output>.cuts.json` (start, end, text, reason, applied) is written next to the output. Show it to the user and have them listen at the cut points.

For video input the cut output is audio only and no longer lines up with the picture. Use `cuts.json` in the video editor instead.

## Compare before and after

```bash
python scripts/compare.py INPUT OUTDIR [--cut-fillers]
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
- No multitrack (crosstalk removal, ducking), no silence/cough/music cutting, no transcript or chapter export.
- Filler detection is only as good as Whisper's transcript, which is weakest for Egyptian Arabic fillers. The verbatim prompt helps but doesn't guarantee fillers are transcribed.
- Short pauses (under ~0.5 s) keep speech gain on purpose, to protect word edges.
