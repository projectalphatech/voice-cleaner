"""Find and cut fillers (um, uh, ممم, آآ) and stutters (I- I, ب ب بس) using Whisper word timestamps.

Usage: python disfluency.py INPUT        # print detected cuts as JSON
       python disfluency.py --selfcheck  # rule + crossfade tests, no model needed
Needs `pip install faster-whisper`. Model: WHISPER_MODEL (default large-v3-turbo), downloaded once.
"""
import functools
import json
import os
import re
import sys

import numpy as np

import clean

SR = clean.SR
EN_FILLERS = {"um", "uh", "uhm", "er", "erm", "ah", "hm", "m"}  # after collapsing repeated letters
AR_ALEF = set("اأإآءٱ")
TASHKEEL = re.compile(r"[ً-ْٰـ]")  # harakat, dagger alef, tatweel
# Whisper drops fillers unless the prompt shows them being transcribed verbatim
PROMPTS = {
    "en": "Umm, uh, so, I- I mean, like, hmm, you know, uh, it's- it's fine.",
    "ar": "ممم، آآ، يعني، أنا أنا قصدي، إأ، ب بس، مم، ماشي.",
}


@functools.lru_cache(maxsize=1)
def model():
    from faster_whisper import WhisperModel
    return WhisperModel(os.environ.get("WHISPER_MODEL", "large-v3-turbo"), device="auto",
                        compute_type=os.environ.get("WHISPER_COMPUTE", "int8"))


def transcribe(x):
    """Return ([(start, end, text)], language) for float32 (n, ch) audio at SR."""
    m16 = clean.filt(x.mean(1, keepdims=True), "aresample=16000")[:, 0]
    lang = model().detect_language(m16)[0]
    segs, _ = model().transcribe(m16, language=lang, word_timestamps=True, initial_prompt=PROMPTS.get(lang, PROMPTS["en"]),
                                 condition_on_previous_text=False)
    return [(w.start, w.end, w.word.strip()) for s in segs for w in (s.words or [])], lang


def voiced(x):
    """Unsmoothed 20 ms frames within 30 dB of the loudest speech."""
    hop = SR // 50
    m = x.mean(1)
    n = len(m) // hop
    db = 10 * np.log10((m[: n * hop].reshape(n, hop) ** 2).mean(1) + 1e-12)
    return db > (np.percentile(db, 95) - 30 if n else 0)


def norm(w):
    return re.sub(r"[^\w-]", "", TASHKEEL.sub("", w.lower()))


def is_filler(core):
    if re.sub(r"(.)\1+", r"\1", core) in EN_FILLERS:
        return True
    # آآ / إأ / ممم: only alef-hamza letters or only meem. آه, أم, إيه and يعني carry meaning and stay.
    return len(core) >= 2 and (set(core) <= AR_ALEF or set(core) == {"م"})


def is_stutter(word, core, nxt):
    if not core or not nxt:
        return False
    if core == nxt:
        return len(core) <= 3  # "the the", "أنا أنا"; longer repeats ("very very", "واحدة واحدة") are usually on purpose
    arabic_letter = len(core) == 1 and "؀" <= core <= "ۿ"
    return nxt.startswith(core) and len(core) <= 3 and (word.endswith("-") or arabic_letter)


def find_cuts(words, mask, total, cut_untranscribed=False):
    """words: [(start, end, text)] in seconds; mask: voiced() frames; total: audio length in seconds."""
    cores = [norm(w).strip("-") for _, _, w in words]
    cuts = []
    for i, (s, e, w) in enumerate(words):
        nxt = cores[i + 1] if i + 1 < len(words) else ""
        reason = "filler" if is_filler(cores[i]) else "stutter" if is_stutter(norm(w), cores[i], nxt) else None
        if reason:
            lo = words[i - 1][1] if i else 0.0
            hi = words[i + 1][0] if i + 1 < len(words) else total
            cuts.append({"start": max(lo, s - 0.02), "end": min(hi, e + 0.02), "text": w, "reason": reason, "applied": True})
    for (_, a, _), (b, _, _) in zip(words, words[1:]):  # speech energy Whisper left untranscribed
        if 0.2 <= b - a <= 1.2:
            inner = mask[int((a + 0.05) * 50): int((b - 0.05) * 50)]
            run = max((len(r) for r in "".join("1" if v else "0" for v in inner).split("0")), default=0)
            if run >= 10:
                cuts.append({"start": a, "end": b, "text": "", "reason": "untranscribed", "applied": cut_untranscribed})
    cuts.sort(key=lambda c: c["start"])
    merged = []
    for c in cuts:
        if merged and c["start"] <= merged[-1]["end"] and c["applied"] == merged[-1]["applied"]:
            m = merged[-1]
            m["end"], m["text"] = max(m["end"], c["end"]), (m["text"] + " " + c["text"]).strip()
            m["reason"] = m["reason"] if m["reason"] == c["reason"] else m["reason"] + "+" + c["reason"]
        else:
            merged.append(dict(c))
    return [c for c in merged if c["end"] - c["start"] >= 0.08]


def apply_cuts(x, spans, fade=0.01):
    """Remove (start, end) second spans; each join is a 10 ms equal-power crossfade (output loses fade per join too)."""
    pieces, pos = [], 0
    for s, e in sorted(spans):
        pieces.append(x[pos:int(s * SR)])
        pos = max(pos, int(e * SR))
    pieces.append(x[pos:])
    out = pieces[0]
    for p in pieces[1:]:
        n = min(int(fade * SR), len(out), len(p))
        t = np.linspace(0, np.pi / 2, n)[:, None]
        out = np.concatenate([out[: len(out) - n], out[len(out) - n:] * np.cos(t) + p[:n] * np.sin(t), p[n:]])
    return out.astype(np.float32)


def detect(x, cut_untranscribed=False):
    words, lang = transcribe(x)
    return find_cuts(words, voiced(x), len(x) / SR, cut_untranscribed), lang


def selfcheck():
    w = lambda *ws: [(i * 0.5, i * 0.5 + 0.4, t) for i, t in enumerate(ws)]
    texts = lambda cuts: [(c["text"], c["reason"]) for c in cuts]
    mask = np.zeros(1000, bool)

    got = texts(find_cuts(w("So", "umm,", "I-", "I", "think", "uh", "the", "the", "very", "very", "end."), mask, 6))
    assert got == [("umm,", "filler"), ("I-", "stutter"), ("uh", "filler"), ("the", "stutter")], got
    got = texts(find_cuts(w("ممم", "أنا", "ب", "بس", "آه", "يعني", "أم", "إيه", "آآ", "تمام"), mask, 6))
    assert ("ممم", "filler") in got and ("ب", "stutter") in got and ("آآ", "filler") in got, got
    assert not any(t in ("آه", "يعني", "أم", "إيه", "أنا", "بس", "تمام") for t, _ in got), got
    assert texts(find_cuts(w("I", "in", "the", "end"), mask, 3)) == []  # "I" is a word, not a fragment

    gapped = [(0.0, 0.4, "hello"), (1.2, 1.6, "world")]
    mask[25:55] = True  # 600 ms of voice inside the gap
    cuts = find_cuts(gapped, mask, 2)
    assert len(cuts) == 1 and cuts[0]["reason"] == "untranscribed" and not cuts[0]["applied"], cuts

    x = np.sin(2 * np.pi * 200 * np.arange(SR * 3) / SR).astype(np.float32)[:, None]
    y = apply_cuts(x, [(1.0, 1.5), (2.0, 2.2)])
    assert len(y) == len(x) - int(0.5 * SR) - int(0.2 * SR) - 2 * int(0.01 * SR), len(y)
    assert np.abs(np.diff(y[:, 0])).max() <= 2 * np.abs(np.diff(x[:, 0])).max(), "click at a join"
    print("disfluency selfcheck ok")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selfcheck"]:
        selfcheck()
    elif len(sys.argv) == 2:
        cuts, lang = detect(clean.decode(sys.argv[1]))
        print(json.dumps({"language": lang, "cuts": cuts}, ensure_ascii=False, indent=1))
    else:
        sys.exit(__doc__)
