"""Clean a recording and compare before/after: loudness, peak, noise floor, speech spread, spectrograms.

Usage: python compare.py IN OUTDIR [--cut-fillers]
"""
import os
import re
import sys

import numpy as np

import clean


def stats(path):
    err = clean.ff(["-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"], loglevel="info").stderr.decode()
    last = lambda pat: float(re.findall(pat, err)[-1])
    x = clean.decode(path).mean(1)
    hop = clean.SR // 20
    n = len(x) // hop
    db = 10 * np.log10(np.convolve((x[: n * hop].reshape(n, hop) ** 2).mean(1), np.ones(8) / 8, "same") + 1e-12)
    floor = np.percentile(db, 10)
    speech = db[db > floor + 10]
    return {
        "loudness LUFS": last(r"I:\s+(-?[\d.]+) LUFS"),
        "true peak dBTP": last(r"Peak:\s+(-?[\d.]+) dBFS"),
        "loudness range LU": last(r"LRA:\s+(-?[\d.]+) LU"),
        "noise floor dBFS": floor,
        "speech level dBFS": np.median(speech),
        "speech-to-noise dB": np.median(speech) - floor,
        "speech spread p10-p90 dB": np.percentile(speech, 90) - np.percentile(speech, 10),
        "hum Hz": clean.detect_hum(clean.decode(path)) or 0,
    }


def main(src, out, cut_fillers=False):
    os.makedirs(out, exist_ok=True)
    dst = os.path.join(out, "after.wav")
    info = clean.clean(src, dst, target=-16, cut_fillers=cut_fillers)
    clean.ff(["-i", src, "-c:a", "pcm_s24le", os.path.join(out, "before.wav")])
    before, after = stats(src), stats(dst)
    print(f"chain: {info}\n{'metric':28}{'before':>10}{'after':>10}")
    for k in before:
        print(f"{k:28}{before[k]:10.1f}{after[k]:10.1f}")
    spec = "showspectrumpic=s=1600x360:legend=1:scale=log:fscale=log:color=intensity"
    pics = []
    for name, p in (("before", src), ("after", dst)):
        pics.append(os.path.join(out, f"spec-{name}.png"))
        clean.ff(["-i", p, "-lavfi", spec, pics[-1]])
    clean.ff(["-i", pics[0], "-i", pics[1], "-filter_complex", "vstack", os.path.join(out, "spectrogram.png")])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], "--cut-fillers" in sys.argv[3:])
