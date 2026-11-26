"""Audio side of Accurate Subtitles (Utility/10_accurate_subtitles.py).

Runs in the machine-learning venv (mlx-whisper, torch, torchaudio, num2words),
never inside Resolve:

    venv/bin/python subtitle_worker.py transcribe job.json
    venv/bin/python subtitle_worker.py align job.json

transcribe  Whisper large-v3 for the TEXT (optionally a turbo pass for its
            punctuation), number fragments merged, ASR clean-up applied.
            Writes <id>_words.json and <id>_transcript.txt - one line per
            Whisper segment, so the text can be edited before aligning.
align       Forced alignment (torchaudio MMS_FA) of the transcript against
            the audio, onset snapping, John's cue rules, then the SRT
            (<id>_final.srt), <id>_cues.json (exact frames) and a report.

Whisper word timings run early, so they are only used to cut the audio into
chunks at quiet points; every cue time comes from the aligner and the audio.

Progress goes to stdout, one line each:
    @@P <0..1> <message>      progress of the current stage
    @@R <json>                the result, last line on success
Anything else on stdout/stderr is log text.
"""

import difflib
import importlib
import json
import math
import os
import re
import sys
import types
import wave

import numpy as np

SR = 16000
LARGE = "mlx-community/whisper-large-v3-mlx"
TURBO = "mlx-community/whisper-large-v3-turbo"

WEAK = set("""a an the to of and or but in on at for with my your i is was that this so if as
by than just it's it who no into about because has had have we they you he she""".split())
STUTTER = set("""a an the i and to of it is that we you he she they so but in on my this was
if like what i'm it's or as at for be""".split())
BACKCHANNEL = set("yeah yes yep okay ok right sure mhm mm-hmm uh-huh wow nice cool exactly".split())
FILLERS = r"(?:um+|uh+|erm+|hmm+)"
LETTERS = {c: n for c, n in zip("abcdefghijklmnopqrstuvwxyz", (
    "ay bee see dee ee ef gee aitch eye jay kay el em en oh pee cue ar ess tee you vee "
    "doubleyou ex why zee").split())}
CLAUSE_END = re.compile(r"[,.?!:;…]+$")


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------

def progress(p, msg=""):
    print(f"@@P {max(0.0, min(1.0, p)):.4f} {msg}", flush=True)


def log(msg):
    print(msg, flush=True)


def load_wav(path):
    """16-bit PCM WAV -> float32 mono at 16 kHz."""
    with wave.open(path, "rb") as w:
        sr, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        data = w.readframes(n)
    if width != 2:
        raise RuntimeError(f"{os.path.basename(path)}: expected 16-bit audio, got {8 * width}-bit")
    a = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    if sr != SR:
        t = np.arange(int(len(a) * SR / sr)) * (sr / SR)
        a = np.interp(t, np.arange(len(a)), a).astype(np.float32)
    return a


def envelope(audio):
    """5 ms RMS envelope in dB, and its loud/quiet threshold (15th pct + 12 dB)."""
    hop = SR // 200
    n = len(audio) // hop
    frames = audio[:n * hop].reshape(n, hop)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    return db, float(np.percentile(db, 15) + 12.0)


def fmt_time(sec):
    m, s = divmod(max(0.0, sec), 60)
    return f"{int(m)}:{s:05.2f}"


def norm(word):
    return re.sub(r"[^a-z0-9%']", "", word.lower())


# --------------------------------------------------------------------------
# transcribe
# --------------------------------------------------------------------------

def _whisper(audio, repo, lo, hi, label, **kw):
    """mlx_whisper.transcribe, with its progress bar turned into @@P lines."""
    import mlx_whisper
    module = importlib.import_module("mlx_whisper.transcribe")

    class Bar:
        def __init__(self, total=0, **_):
            self.total, self.n = max(1, total or 1), 0

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def update(self, k):
            self.n += k
            progress(lo + (hi - lo) * min(1.0, self.n / self.total), label)

    module.tqdm = types.SimpleNamespace(tqdm=Bar)
    progress(lo, label + " (loading model)")
    return mlx_whisper.transcribe(audio, path_or_hf_repo=repo, verbose=False,
                                  condition_on_previous_text=False, **kw)


def merge_number_fragments(words):
    """'10' + ',000' -> '10,000', '4' + '.0' -> '4.0', '5' + '%' -> '5%'."""
    out = []
    for w in words:
        text = w["word"]
        bare = text.strip()
        if out and re.search(r"\d$", out[-1]["word"].strip()) and (
                (not text.startswith(" ") and re.match(r"^([.,]\d|\d)", bare)) or bare == "%"):
            prev = out[-1]
            prev["word"] = prev["word"].rstrip() + bare
            prev["end"] = max(prev["end"], w["end"])
            prev["probability"] = min(prev["probability"], w["probability"])
            continue
        out.append(dict(w))
    return out


def transfer_punctuation(segments, turbo_text):
    """Copy turbo's sentence punctuation onto the large-v3 words (large-v3
    drops some), and restore runs of 3+ words large-v3 left out mid-text."""
    flat = [(si, wi) for si, seg in enumerate(segments) for wi in range(len(seg["words"]))]
    a = [norm(segments[si]["words"][wi]["word"]) for si, wi in flat]
    tb = turbo_text.split()
    b = [norm(t) for t in tb]
    restored, copied = [], 0
    inserts = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                si, wi = flat[i1 + k]
                w = segments[si]["words"][wi]
                pt = CLAUSE_END.search(tb[j1 + k])
                if pt and not w["word"].endswith(pt.group(0)):
                    w["word"] = CLAUSE_END.sub("", w["word"]) + pt.group(0)
                    copied += 1
                # large-v3 sometimes writes a whole stretch in lowercase
                mine = re.search(r"[A-Za-z']+", w["word"])
                theirs = re.search(r"[A-Za-z']+", tb[j1 + k])
                if mine and theirs and mine.group(0).islower() and not theirs.group(0).islower() \
                        and mine.group(0).lower() == theirs.group(0).lower():
                    w["word"] = w["word"][:mine.start()] + theirs.group(0) + w["word"][mine.end():]
        elif op == "insert" and j2 - j1 >= 3 and 0 < i1 < len(a):
            inserts.append((i1, tb[j1:j2]))
    for i1, new in reversed(inserts):
        si, wi = flat[i1 - 1]
        seg = segments[si]
        t = seg["words"][wi]["end"]
        for k, word in enumerate(new):
            seg["words"].insert(wi + 1 + k, {"word": " " + word, "start": t, "end": t,
                                             "probability": 0.0, "restored": True})
        restored.append({"time": t, "text": " ".join(new)})
    return copied, restored


_UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
          "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
_NUM_WORD = r"(?:%s|%s)" % ("|".join(_UNITS), "|".join(_TENS))
PERCENT_WORDS = r"\b(%s(?:[ -]%s)?|one hundred|a hundred) percent\b" % (_NUM_WORD, _NUM_WORD)


def words_to_number(words):
    """'five' -> 5, 'twenty-five' -> 25, 'a hundred' -> 100."""
    total = 0
    for w in re.split(r"[ -]", words.lower()):
        if w in _UNITS:
            total += _UNITS.index(w)
        elif w in _TENS:
            total += 20 + 10 * _TENS.index(w)
        elif w == "hundred":
            total = max(1, total) * 100
    return total


def drop_repeat_across_lines(lines):
    """Whisper often repeats the last words of a segment at the start of the
    next ('... I mean' / 'I mean, they ...'): keep the second copy."""
    for i in range(len(lines) - 1):
        a, b = lines[i].split(), lines[i + 1].split()
        for n in (3, 2, 1):
            if len(a) > n and len(b) >= n and [norm(x) for x in a[-n:]] == [norm(x) for x in b[:n]] \
                    and all(norm(x) for x in a[-n:]) and (n > 1 or norm(a[-1]) in STUTTER):
                lines[i] = " ".join(a[:-n]).rstrip(" ,")
                break
    return lines


def capitalise(lines):
    """'i' -> 'I', and a capital after a full stop (large-v3 has lowercase
    stretches the turbo pass can't always fix)."""
    sentence_start = True
    out = []
    for line in lines:
        toks = line.split()
        for k, t in enumerate(toks):
            t = re.sub(r"^i(?=$|'|[,.?!:;])", "I", t)
            if sentence_start and t[:1].islower():
                t = t[0].upper() + t[1:]
            toks[k] = t
            sentence_start = bool(re.search(r"[.?!]['\"”]?$", t))
        out.append(" ".join(toks))
    return out


def clean_text(text, replacements):
    """ASR clean-up of one segment's text. Keeps punctuation (it marks the
    clause breaks); the cue builder removes it later."""
    for wrong, right in replacements:
        if wrong.strip():
            text = re.sub(r"(?<![\w'])" + re.escape(wrong.strip()) + r"(?![\w'])",
                          right.strip(), text, flags=re.I)
    text = re.sub(r"\b" + FILLERS + r"\b[,.]?\s*", "", text, flags=re.I)
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)                     # 10,000 -> 10000
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    text = re.sub(PERCENT_WORDS, lambda m: f"{words_to_number(m.group(1))}%", text, flags=re.I)
    text = re.sub(r"(?<=\d) percent\b", "%", text, flags=re.I)              # 5 percent -> 5%
    text = re.sub(r"\b((?:[A-Za-z]\.){2,})", lambda m: m.group(1).replace(".", "").upper(), text)
    text = re.sub(r"\s*(?:—|–|--| - )\s*", ", ", text)        # dashes = a break
    text = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", " ", text)              # day-to-day -> day to day
    toks = text.split()
    out = []
    for t in toks:                                                       # "the the", "I, I"
        if out and norm(out[-1]) == norm(t) and norm(t) in STUTTER and not CLAUSE_END.search(t[:-1] or "x"):
            out[-1] = t
            continue
        out.append(t)
        if len(out) >= 4 and [norm(x) for x in out[-4:-2]] == [norm(x) for x in out[-2:]] \
                and all(norm(x) for x in out[-2:]):                     # "I was I was"
            del out[-4:-2]
    text = " ".join(out)
    text = re.sub(r"\s+([,.?!:;%])", r"\1", text)
    text = re.sub(r"([,.?!:;])\1+", r"\1", text)
    return text.strip(" ,")


def is_backchannel(seg, prev, nxt):
    """An interviewer's 'yeah' / 'okay' spoken over the guest."""
    words = [norm(w["word"]) for w in seg["words"]]
    if not words or len(words) > 3 or any(w not in BACKCHANNEL for w in words):
        return False
    t0, t1 = seg["words"][0]["start"], seg["words"][-1]["end"]
    if t1 - t0 > 1.5:
        return False
    overlaps_prev = prev is not None and t0 < prev["words"][-1]["end"] + 0.25
    overlaps_next = nxt is not None and t1 > nxt["words"][0]["start"] - 0.25
    return overlaps_prev and overlaps_next


def cmd_transcribe(job):
    audio = load_wav(job["wav"])
    duration = len(audio) / SR
    log(f"Audio: {fmt_time(duration)}")
    lang = job.get("language") or None
    prompt = job.get("prompt") or None
    turbo = bool(job.get("turbo", True))

    r = _whisper(audio, LARGE, 0.0, 0.75 if turbo else 1.0, "Transcribing (large-v3)",
                 word_timestamps=True, language=lang, initial_prompt=prompt)
    segments = []
    for s in r.get("segments", []):
        words = [w for w in s.get("words") or [] if w.get("word", "").strip()]
        if words:
            segments.append({"words": merge_number_fragments(words)})

    copied, restored = 0, []
    if turbo and segments:
        r2 = _whisper(audio, TURBO, 0.75, 1.0, "Punctuation pass (turbo)",
                      word_timestamps=False, language=lang, initial_prompt=prompt)
        copied, restored = transfer_punctuation(segments, r2.get("text", ""))
        log(f"Turbo pass: punctuation copied onto {copied} word(s)"
            + (f", {len(restored)} dropped phrase(s) restored" if restored else ""))

    replacements = [tuple(x) for x in job.get("replacements") or []]
    lines, dropped = [], 0
    for i, seg in enumerate(segments):
        prev = segments[i - 1] if i else None
        nxt = segments[i + 1] if i + 1 < len(segments) else None
        if job.get("drop_backchannels", True) and is_backchannel(seg, prev, nxt):
            lines.append("")
            dropped += 1
            continue
        lines.append(clean_text("".join(w["word"] for w in seg["words"]), replacements))
    lines = capitalise(drop_repeat_across_lines(lines))
    if dropped:
        log(f"Dropped {dropped} overlapping backchannel(s)")

    lowconf = [{"time": w["start"], "word": w["word"].strip()}
               for seg in segments for w in seg["words"]
               if not w.get("restored") and w.get("probability", 1) < 0.4]
    with open(job["words_json"], "w", encoding="utf-8") as f:
        json.dump({"duration": duration, "segments": segments, "lowconf": lowconf,
                   "restored": restored, "wav_size": os.path.getsize(job["wav"]),
                   "settings": job.get("cache_key")}, f, ensure_ascii=False, indent=1)
    with open(job["transcript"], "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    progress(1.0, "Transcribed")
    return {"segments": len(segments), "words": sum(len(s["words"]) for s in segments),
            "lowconf": len(lowconf), "restored": len(restored)}


# --------------------------------------------------------------------------
# align
# --------------------------------------------------------------------------

def _num_words(n):
    from num2words import num2words
    return num2words(n).replace("-", " ").replace(",", "")


def spoken(token, say_as_word):
    """A display token -> the words actually said, lowercase a-z."""
    t = token.strip("\"'()[]{}“”‘’")
    t = CLAUSE_END.sub("", t)
    out = []
    pct = t.endswith("%")
    if pct:
        t = t[:-1]
    m_ord = re.fullmatch(r"(\d+)(st|nd|rd|th)", t, re.I)
    if t.startswith("$"):
        t = t[1:]
        pct = False
        suffix = ["dollars"]
    else:
        suffix = []
    if m_ord:
        from num2words import num2words
        out += num2words(int(m_ord.group(1)), to="ordinal").replace("-", " ").split()
    elif re.fullmatch(r"\d+", t):
        n = int(t)
        if 1100 <= n <= 2099 and len(t) == 4 and not pct and not suffix:
            from num2words import num2words
            out += num2words(n, to="year").replace("-", " ").split()
        else:
            out += _num_words(n).split()
    elif re.fullmatch(r"\d*\.\d+", t):
        whole, frac = t.split(".")
        if whole in ("", "0"):
            out += ["point"] + [_num_words(int(d)) for d in frac]           # 0.01 -> point zero one
        else:
            out += _num_words(int(whole)).split()
            if frac == "0":
                out += ["oh"]                                               # 4.0 -> four oh
            elif len(frac) == 2 and frac[0] != "0":
                out += _num_words(int(frac)).split()                        # 3.97 -> three ninety seven
            else:
                out += [("oh" if d == "0" else _num_words(int(d))) for d in frac]
    elif re.fullmatch(r"[A-Z]{2,6}(?:'?s)?", t) and t.rstrip("s").rstrip("'").upper() not in say_as_word:
        core = re.sub(r"'?s$", "", t) if re.search(r"[A-Z]'?s$", t) else t
        out += [LETTERS[c] for c in core.lower()]                           # USMLE -> you ess em el ee
        if core != t:
            out[-1] += "s"
    elif t == "&":
        out += ["and"]
    else:
        for part in re.split(r"[\s/]+", t):
            if re.fullmatch(r"\d+", part):
                out += _num_words(int(part)).split()
            elif part:
                out.append(part)
    out += (["percent"] if pct else []) + suffix
    words = [re.sub(r"[^a-z]", "", w.lower()) for w in out]
    return [w for w in words if w]


def display_with_punctuation(token):
    """The token as it appears in a cue when punctuation is kept: only
    quotes and dashes go."""
    t = token.replace("\u201c", "").replace("\u201d", "").replace('"', "")
    t = t.replace("\u2019", "'").replace("\u2018", "'")
    t = re.sub(r"^[^\w$%'&\u00bf\u00a1]+", "", t)
    t = re.sub(r"[^\w%'.,?!:;\u2026]+$", "", t)
    return t


def display(token):
    """The token as it appears in a cue: no punctuation, but apostrophes
    inside words and decimals / percent signs inside numbers stay."""
    t = token.replace("“", "").replace("”", "").replace('"', "")
    t = t.replace("’", "'").replace("‘", "'")
    t = re.sub(r"^[^\w$%'&]+", "", t)
    t = re.sub(r"[^\w%']+$", "", t)
    t = re.sub(r"^'+(?=\w{3})", "'", t)
    t = re.sub(r"(?<=\w)[,;:?!](?=\w)", " ", t)
    return t


class Aligner:
    def __init__(self):
        import torch
        import torchaudio
        self.torch = torch
        bundle = torchaudio.pipelines.MMS_FA
        self.model = bundle.get_model(with_star=False)
        self.tokenizer = bundle.get_tokenizer()
        self.align = bundle.get_aligner()
        self.chars = set(bundle.get_dict(star=None))
        self.device = "cpu"
        if torch.backends.mps.is_available():
            try:
                self.model = self.model.to("mps")
                with torch.inference_mode():
                    self.model(torch.zeros(1, SR, device="mps"))
                self.device = "mps"
            except Exception:
                self.model = self.model.to("cpu")
        self.model.eval()

    def __call__(self, audio, words):
        """Spans (start_sec, end_sec) for each word, relative to the audio."""
        torch = self.torch
        with torch.inference_mode():
            x = torch.from_numpy(audio).unsqueeze(0).to(self.device)
            em, _ = self.model(x)
            em = em.cpu()
        spans = self.align(em[0], self.tokenizer(words))
        ratio = len(audio) / em.size(1) / SR
        return [(s[0].start * ratio, s[-1].end * ratio) for s in spans]


def plan_chunks(items, duration, db, max_len=30.0):
    """Group segments into ~30 s chunks, cut at the quietest 100 ms between
    two segments (the aligner can't take a whole interview at once)."""
    def quiet_point(lo, hi):
        a, b = int(max(0.0, lo) * 200), int(min(duration, hi) * 200)
        if b - a < 21:
            return (lo + hi) / 2
        smooth = np.convolve(db[a:b], np.ones(20) / 20, mode="valid")
        return (a + int(np.argmin(smooth)) + 10) / 200.0

    chunks, cur, start = [], [], 0.0
    for it in items:
        if cur and it["t1"] - start > max_len:
            prev_end = cur[-1]["t1"]
            cut = quiet_point(max(start + 0.2, prev_end - 0.15), max(it["t0"] + 0.8, prev_end + 0.3))
            chunks.append((start, cut, cur))
            cur, start = [], cut
        cur.append(it)
    if cur:
        chunks.append((start, duration, cur))
    return chunks


def snap_onsets(tokens, db, thr):
    """Move a start onto the end of a >=120 ms silence within -0.12..+0.25 s."""
    loud = db > thr
    onsets, run = [], 0
    for i, v in enumerate(loud):
        if v:
            if run >= 24:
                onsets.append(i / 200.0)
            run = 0
        else:
            run += 1
    onsets = np.array(onsets)
    if not len(onsets):
        return 0
    moved = 0
    for t in tokens:
        if t["start"] is None:
            continue
        near = onsets[(onsets >= t["start"] - 0.12) & (onsets <= t["start"] + 0.25)]
        if len(near):
            best = float(near[np.argmin(np.abs(near - t["start"]))])
            if best < t["end"]:
                t["start"] = best
                moved += 1
    return moved


def split_clause(texts, max_chars):
    """DP split of one clause into cues (John's scoring)."""
    n = len(texts)
    best = [0.0] + [math.inf] * n
    back = [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(j - 1, -1, -1):
            length = len(" ".join(texts[i:j]))
            if length > max_chars and j - i > 1:
                break
            cost = 1 + 0.02 * (max_chars - length) ** 2
            if j < n and re.sub(r"[^\w']", "", texts[j - 1].lower()) in WEAK:
                cost += 7
            if length <= 4:
                cost += 3
            if length > max_chars:
                cost += 5
            if best[i] + cost < best[j]:
                best[j], back[j] = best[i] + cost, i
    cuts, j = [], n
    while j > 0:
        cuts.append((back[j], j))
        j = back[j]
    return cuts[::-1]


def srt_time(frame, num, den):
    # Rounded UP to the millisecond: Resolve reads the time back to the same
    # frame whether it rounds or truncates.
    ms = max(0, math.ceil(frame * den * 1000.0 / num - 1e-6))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cmd_align(job):
    num, den = job["fps"]
    fps = num / den
    max_chars = int(job.get("max_chars", 14))
    lead = float(job.get("lead_frames", 4))
    say_as_word = {w.strip().upper() for w in job.get("say_as_word") or [] if w.strip()}
    shown = display_with_punctuation if job.get("keep_punctuation") else display

    audio = load_wav(job["wav"])
    duration = len(audio) / SR
    db, thr = envelope(audio)
    with open(job["words_json"], encoding="utf-8") as f:
        wj = json.load(f)
    with open(job["transcript"], encoding="utf-8") as f:
        lines = f.read().split("\n")
    segs = wj["segments"]
    while len(lines) > len(segs) and not lines[-1].strip():
        lines.pop()
    if len(lines) != len(segs):
        raise RuntimeError(f"The transcript has {len(lines)} lines but there are {len(segs)} "
                           f"segments - keep one line per segment (empty a line to drop it).")

    # Tokens, with each segment's Whisper span (used only to cut chunks).
    items, tokens = [], []
    for li, (line, seg) in enumerate(zip(lines, segs)):
        raw = [t for t in line.split() if display(t) or spoken(t, say_as_word)]
        if not raw:
            continue
        timed = [w for w in seg["words"] if not w.get("restored")]
        t0, t1 = timed[0]["start"], timed[-1]["end"]
        it = {"t0": t0, "t1": t1, "tokens": []}
        for k, t in enumerate(raw):
            tok = {"raw": t, "text": shown(t) if display(t) else "", "spoken": spoken(t, say_as_word),
                   "line": li, "last_in_line": k == len(raw) - 1, "start": None, "end": None}
            it["tokens"].append(tok)
            tokens.append(tok)
        items.append(it)
    if not tokens:
        raise RuntimeError("The transcript is empty - nothing to subtitle.")

    progress(0.02, "Loading the aligner")
    aligner = Aligner()
    chunks = plan_chunks(items, duration, db)
    failed = []
    for ci, (c0, c1, chunk_items) in enumerate(chunks):
        progress(0.05 + 0.8 * ci / len(chunks), f"Aligning words ({ci + 1}/{len(chunks)})")
        ctoks = [t for it in chunk_items for t in it["tokens"]]
        words, owner = [], []
        for ti, t in enumerate(ctoks):
            for w in t["spoken"]:
                words.append(w)
                owner.append(ti)
        if not words:
            continue
        a, b = int(c0 * SR), int(c1 * SR)
        try:
            spans = aligner(audio[a:b], words)
        except Exception as e:
            failed.append({"time": c0, "error": str(e)})
            # Fall back to Whisper's span for this chunk, spread evenly.
            t0, t1 = chunk_items[0]["t0"], chunk_items[-1]["t1"]
            step = (t1 - t0) / max(1, len(ctoks))
            for k, t in enumerate(ctoks):
                t["start"], t["end"] = t0 + k * step, t0 + (k + 1) * step
            continue
        for (s, e), ti in zip(spans, owner):
            t = ctoks[ti]
            if t["start"] is None:
                t["start"] = c0 + s
            t["end"] = c0 + e
    # Tokens with nothing to say (a lone symbol) borrow their neighbour's time.
    for i, t in enumerate(tokens):
        if t["start"] is None:
            prev = tokens[i - 1]["end"] if i else 0.0
            t["start"] = t["end"] = prev

    progress(0.88, "Snapping to onsets")
    snapped = snap_onsets(tokens, db, thr)

    # Clauses: punctuation, a segment boundary, or a pause of 2 s+.
    progress(0.92, "Building cues")
    clauses, cur = [], []
    for i, t in enumerate(tokens):
        if t["text"]:
            cur.append(t)
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        brk = (CLAUSE_END.search(t["raw"].rstrip("\"'”)")) or t["last_in_line"]
               or nxt is None or nxt["start"] - t["end"] > 2.0)
        if brk and cur:
            clauses.append(cur)
            cur = []
    cues, long_words = [], []
    for clause in clauses:
        texts = [t["text"] for t in clause]
        for i, j in split_clause(texts, max_chars):
            text = " ".join(texts[i:j])
            if len(text) > max_chars:
                long_words.append({"time": clause[i]["start"], "text": text})
            cues.append({"text": text, "start": clause[i]["start"], "word_end": clause[j - 1]["end"]})

    # A cue that starts in silence moves to the first sustained loud frame.
    loud = db > thr
    for c in cues:
        f0 = int(c["start"] * 200)
        if f0 + 12 <= len(loud) and loud[f0:f0 + 12].mean() < 0.3:
            for k in range(f0, min(len(loud) - 4, f0 + 80)):
                if loud[k:k + 4].all():
                    c["start"] = k / 200.0
                    break

    # 2-frame minimum, then the lead, then whole frames that strictly increase.
    two = 2.0 / fps
    for i in range(1, len(cues)):
        cues[i]["start"] = max(cues[i]["start"], cues[i - 1]["start"] + two)
    for c in cues:
        c["target"] = max(0.0, c["start"] - lead / fps)
    for i in range(1, len(cues)):
        cues[i]["target"] = max(cues[i]["target"], cues[i - 1]["target"] + 2.2 / fps)
    frames, pushed = [], []
    for c in cues:
        f = int(round(c["target"] * fps))
        if frames and f < frames[-1] + 2:
            f = frames[-1] + 2
        if abs(f - c["target"] * fps) > 3:
            pushed.append({"time": c["target"], "text": c["text"], "frames": round(f - c["target"] * fps)})
        frames.append(f)
    last_frame = int(math.floor(duration * fps))
    end_last = min(int(round((cues[-1]["word_end"] + 0.3) * fps)), last_frame)
    ends = frames[1:] + [max(end_last, frames[-1] + 2)]

    with open(job["srt"], "w", encoding="utf-8") as f:
        for n, (c, s, e) in enumerate(zip(cues, frames, ends), 1):
            f.write(f"{n}\n{srt_time(s, num, den)} --> {srt_time(e, num, den)}\n{c['text']}\n\n")
    with open(job["cues_json"], "w", encoding="utf-8") as f:
        json.dump({"fps": [num, den], "cues": [{"f": s, "e": e, "text": c["text"]}
                                               for c, s, e in zip(cues, frames, ends)]},
                  f, ensure_ascii=False, indent=1)

    report = [f"{len(cues)} cues, {len(tokens)} words, {snapped} starts snapped to onsets, "
              f"{len(chunks)} alignment chunk(s)."]
    if long_words:
        report.append(f"Over {max_chars} characters (can't be split):")
        report += [f"  {fmt_time(x['time'])}  {x['text']}" for x in long_words]
    if wj.get("lowconf"):
        report.append("Check by ear (Whisper was unsure):")
        report += [f"  {fmt_time(x['time'])}  {x['word']}" for x in wj["lowconf"]]
    if wj.get("restored"):
        report.append("Restored from the turbo pass (large-v3 left them out) - check by ear:")
        report += [f"  {fmt_time(x['time'])}  {x['text']}" for x in wj["restored"]]
    if pushed:
        report.append("Cues that couldn't sit on their word (pushed by the 2-frame minimum):")
        report += [f"  {fmt_time(x['time'])}  {x['text']}  ({x['frames']:+d} frames)" for x in pushed]
    if failed:
        report.append("Chunks the aligner couldn't do (Whisper timing used, check these):")
        report += [f"  {fmt_time(x['time'])}  {x['error']}" for x in failed]
    with open(job["report"], "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
    progress(1.0, "Cues built")
    return {"cues": len(cues), "long": len(long_words), "pushed": len(pushed),
            "failed": len(failed), "lowconf": len(wj.get("lowconf") or []),
            "restored": len(wj.get("restored") or [])}


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("transcribe", "align"):
        print(__doc__)
        return 2
    with open(sys.argv[2], encoding="utf-8") as f:
        job = json.load(f)
    result = (cmd_transcribe if sys.argv[1] == "transcribe" else cmd_align)(job)
    print("@@R " + json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
