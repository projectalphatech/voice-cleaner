"""Voice cleaner chain: high-pass, de-hum, denoise, leveler, loudness + true-peak limit.

Usage: python clean.py IN OUT.mp3 [--target -16] [--no-denoise] [--no-leveler]
       python clean.py --selfcheck
Needs ffmpeg/ffprobe on PATH. Uses DeepFilterNet if `deep-filter` (or DEEPFILTER_CMD) exists.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np

SR = 48000
TARGETS = (-14, -16, -19, -23, -24)  # streaming, podcast, mono podcast, EBU R128, ATSC A/85
FORMATS = {
    "mp3": ["-c:a", "libmp3lame", "-b:a", "192k"],
    "m4a": ["-c:a", "aac", "-b:a", "192k"],
    "flac": ["-c:a", "flac"],
    "wav": ["-c:a", "pcm_s24le"],
}


def ff(args, data=None, loglevel="error"):
    kw = {"input": data} if data is not None else {"stdin": subprocess.DEVNULL}
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", loglevel, "-y", *args], capture_output=True, **kw)
    if p.returncode:
        raise RuntimeError("ffmpeg failed: " + p.stderr.decode(errors="replace")[-400:])
    return p


def channels(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    if p.returncode or not p.stdout.strip():
        raise ValueError("no audio stream found")
    return min(int(p.stdout.split()[0].strip(",")), 2)


def decode(path):
    ch = channels(path)
    raw = ff(["-i", path, "-vn", "-ac", str(ch), "-ar", str(SR), "-f", "f32le", "pipe:1"]).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, ch)


def raw_in(x):
    return ["-f", "f32le", "-ar", str(SR), "-ac", str(x.shape[1]), "-i", "pipe:0"]


def filt(x, chain):
    raw = ff([*raw_in(x), "-af", chain, "-f", "f32le", "pipe:1"], x.astype(np.float32).tobytes()).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, x.shape[1])


def detect_hum(x):
    """Return 50 or 60 when a mains hum line (or a harmonic) stands 20 dB out of its neighbourhood."""
    m = x.mean(1)[: SR * 120]
    if len(m) < SR:
        return None
    spec = np.abs(np.fft.rfft(m * np.hanning(len(m))))
    hz = np.fft.rfftfreq(len(m), 1 / SR)

    def score(f):
        return max(spec[(hz > k * f - 1) & (hz < k * f + 1)].max()
                   / (np.median(spec[(hz > k * f - 15) & (hz < k * f + 15)]) + 1e-12) for k in (1, 2, 3))

    best = max((50, 60), key=score)
    return best if score(best) > 10 else None


def deepfilter(x, exe):
    with tempfile.TemporaryDirectory() as d:
        src, out = os.path.join(d, "in.wav"), os.path.join(d, "out")
        ff([*raw_in(x), "-c:a", "pcm_s16le", src], x.tobytes())
        subprocess.run([exe, "-o", out, src], check=True, capture_output=True)
        return decode(os.path.join(out, "in.wav"))


def frame_db(x):
    """400 ms RMS level in dB every 50 ms, the noise floor, and a speech mask.

    Speech is 10 dB over the floor and within 30 dB of the loudest speech; the second rule stops room tone
    counting as speech when phone recorders gate pauses to digital silence."""
    hop = SR // 20
    m = x.mean(1)
    n = len(m) // hop
    p = (m[: n * hop].reshape(n, hop) ** 2).mean(1)
    db = 10 * np.log10(np.convolve(p, np.ones(8) / 8, mode="same") + 1e-12)
    if not n:
        return db, -120.0, db > 0
    floor = np.percentile(db, 10)
    return db, floor, db > max(floor + 10, np.percentile(db, 95) - 30)


def speech_to_noise(x):
    """Median speech level over median pause level, in dB."""
    db, _, speech = frame_db(x)
    if speech.sum() < 10 or speech.all():
        return 0.0
    return float(np.median(db[speech]) - np.median(db[~speech]))


def smooth(v, a):
    """Zero-phase one-pole smoothing per 50 ms frame: offline, so gain can anticipate word onsets."""
    v = v.copy()
    for order in (range(len(v)), range(len(v) - 1, -1, -1)):
        cur = v[order[0]]
        for i in order:
            cur += a * (v[i] - cur)
            v[i] = cur
    return v


def level(x, makeup=0.0, max_gain=12.0):
    """Ride speech toward one level. Pauses are gated so that after `makeup` dB of later gain they sit at input level."""
    hop = SR // 20
    db, floor, speech = frame_db(x)
    n = len(db)
    if n < 10 or speech.sum() < 10:
        return x
    idx = np.arange(n)
    want = smooth(np.interp(idx, idx[speech], np.clip(np.median(db[speech]) - db[speech], -max_gain, max_gain)), 0.15)
    near = np.convolve(speech, np.ones(9), mode="same") > 0  # speech ±200 ms keeps its gain, so word edges survive
    gate = smooth(np.where(near, 0.0, np.minimum(0.0, -makeup - want)), 0.5)  # fast ramp: short pauses still close
    gain = 10 ** (np.interp(np.arange(len(x)), idx * hop + hop / 2, want + gate) / 20)
    return (x * gain[:, None]).astype(np.float32)


def loudness(x, base="loudnorm=I=-16"):  # input_i does not depend on the target
    err = ff([*raw_in(x), "-af", base + ":print_format=json", "-f", "null", "-"], x.tobytes(), "info").stderr
    m = json.loads(re.findall(rb"\{[^{}]+\}", err)[-1])
    if m["input_i"] == "-inf":
        raise ValueError("input is silent")
    return m


def loudnorm(x, dst, target, fmt):
    tp = -1.0 if fmt in ("wav", "flac") else -1.5  # lossy codecs overshoot
    base = f"loudnorm=I={target}:TP={tp}:LRA=11"
    m = loudness(x, base)
    chain = (f"{base}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
             f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true,aresample={SR}")
    ff([*raw_in(x), "-af", chain, *FORMATS[fmt], dst], x.tobytes())


def measure(path):
    err = ff(["-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"], loglevel="info").stderr.decode()
    return (float(re.findall(r"I:\s+(-?[\d.]+) LUFS", err)[-1]),
            float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", err)[-1]))


def clean(src, dst, target=-16, denoise=True, leveler=True, denoise_db=12, cut_fillers=False, cut_untranscribed=False):
    fmt = os.path.splitext(dst)[1][1:].lower()
    if fmt not in FORMATS or target not in TARGETS:
        raise ValueError("unsupported format or loudness target")
    x = decode(src)
    if not len(x):
        raise ValueError("empty audio")
    hum = detect_hum(x)
    chain = ["highpass=f=80"]  # ponytail: fixed 80 Hz; Auphonic adapts per segment
    chain += [f"bandreject=f={hum * k}:t=h:w=2" for k in range(1, 7)] if hum else []
    x = filt(x, ",".join(chain))
    extra, cuts = {}, None
    if cut_fillers:
        import disfluency
        cuts, extra["language"] = disfluency.detect(x, cut_untranscribed)
        n = len(x)
    snr = speech_to_noise(x)
    denoise = denoise and snr <= 40  # already clean: a denoiser could only blur the voice
    df = denoise and (os.environ.get("DEEPFILTER_CMD") or shutil.which("deep-filter"))
    if df:
        x = deepfilter(x, df)
    elif denoise:
        x = filt(x, f"afftdn=nr={denoise_db}:tn=1")
    if cuts is not None:
        if len(x) != n:  # cut times would no longer line up
            for c in cuts:
                c["applied"] = False
            extra["cuts_skipped"] = "denoiser changed the audio length"
        spans = [(c["start"], c["end"]) for c in cuts if c["applied"]]
        x = disfluency.apply_cuts(x, spans)
        with open(os.path.splitext(dst)[0] + ".cuts.json", "w", encoding="utf-8") as f:
            json.dump({"language": extra["language"], "cuts": cuts}, f, ensure_ascii=False, indent=1)
        extra.update(cuts=len(spans), cut_seconds=round(sum(e - s for s, e in spans), 2))
    if leveler:
        # pauses must cancel the loudness gain applied later, so measure that gain on a first leveler pass
        x = level(x, target - float(loudness(level(x))["input_i"]))
    loudnorm(x, dst, target, fmt)
    lufs, peak = measure(dst)
    return {"hum_hz": hum, "speech_to_noise": round(snr, 1),
            "denoiser": "deepfilternet" if df else ("afftdn" if denoise else None), "lufs": lufs, "true_peak": peak, **extra}


def selfcheck():
    rng = np.random.default_rng(0)

    def talk(db, secs):  # voice-band noise with a 4 Hz syllable envelope
        n = int(SR * secs)
        v = filt(rng.standard_normal((n, 1)).astype(np.float32), "bandpass=f=1000:t=h:w=1500")[:, 0]
        v = v * 0.5 * (1 + np.sin(2 * np.pi * 4 * np.arange(n) / SR))
        return v / np.sqrt((v ** 2).mean()) * 10 ** (db / 20)

    gap = np.zeros(SR)
    sig = np.concatenate([gap, talk(-18, 8), gap, talk(-32, 8), gap, talk(-18, 8), gap])
    quiet_room = sig + 10 ** (-95 / 20) * rng.standard_normal(len(sig))
    assert speech_to_noise(quiet_room[:, None]) > 40, "clean input must skip the denoiser"
    t = np.arange(len(sig)) / SR
    sig += 0.01 * np.sin(2 * np.pi * 50 * t) + 10 ** (-55 / 20) * rng.standard_normal(len(sig))
    x = sig[:, None].astype(np.float32)
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.wav"), os.path.join(d, "out.wav")
        ff([*raw_in(x), "-c:a", "pcm_f32le", src], x.tobytes())
        info = clean(src, dst, target=-16)
        y = decode(dst)[:, 0]

    def rms(a, b):
        return 10 * np.log10((y[int(a * SR):int(b * SR)] ** 2).mean())

    gap_db = rms(1, 9) - rms(10, 18)  # 14 dB apart at input
    pause_db = rms(0.1, 0.6)  # input noise is -55 dBFS; must not rise with the makeup gain
    assert info["hum_hz"] == 50 and info["denoiser"] == "afftdn", info
    assert pause_db < -55 + 3, pause_db
    assert abs(info["lufs"] + 16) < 1, info
    assert info["true_peak"] <= -0.5, info
    assert abs(gap_db) < 4, gap_db
    print(f"selfcheck ok: {info}, speaker gap {gap_db:.1f} dB (input 14 dB), pause {pause_db:.1f} dBFS (input -55)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--target", type=int, default=-16, choices=TARGETS)
    ap.add_argument("--no-denoise", action="store_true")
    ap.add_argument("--no-leveler", action="store_true")
    ap.add_argument("--cut-fillers", action="store_true", help="remove ums, ahs and stutters (needs faster-whisper)")
    ap.add_argument("--cut-untranscribed", action="store_true", help="also cut voiced gaps Whisper left untranscribed")
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()
    if a.selfcheck:
        selfcheck()
    elif a.src and a.dst:
        print(clean(a.src, a.dst, a.target, not a.no_denoise, not a.no_leveler,
                    cut_fillers=a.cut_fillers or a.cut_untranscribed, cut_untranscribed=a.cut_untranscribed))
    else:
        ap.error("give SRC and DST, or --selfcheck")
