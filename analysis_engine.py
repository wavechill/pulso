"""Music transcription and percussion analysis for Pulso's local rhythm charts."""
from __future__ import annotations

import os
import subprocess
from bisect import bisect_left
from functools import lru_cache
from pathlib import Path

import numpy as np
import onnxruntime as ort

SAMPLE_RATE = 22050
HOP = 256
FPS = SAMPLE_RATE / HOP
MODEL = Path(__file__).parent / "models" / "basic-pitch" / "nmp.onnx"
ENGINE = "Pulso 2 · batida e melodia (Basic Pitch)"
VERSION = 2


def _decode(path):
    command = [
        os.environ.get("FFMPEG_PATH") or "ffmpeg", "-nostdin", "-v", "error",
        "-protocol_whitelist", "file,pipe", "-i", str(Path(path).resolve()),
        "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "f32le", "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=300,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError as exc:
        raise ValueError("O FFmpeg precisa estar instalado para ler a faixa.") from exc
    if result.returncode:
        raise ValueError("O arquivo não contém uma faixa de áudio compatível.")
    audio = np.frombuffer(result.stdout, dtype="<f4")
    if len(audio) < SAMPLE_RATE or not np.isfinite(audio).all():
        raise ValueError("A música precisa conter pelo menos um segundo de áudio válido.")
    if float(np.max(np.abs(audio))) < 0.0001:
        raise ValueError("A faixa está silenciosa. Escolha uma gravação com som.")
    return audio


@lru_cache(maxsize=1)
def _session():
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    return ort.InferenceSession(str(MODEL), sess_options=options,
                                providers=["CPUExecutionProvider"])


def _melody(audio, progress):
    # Keep exactly 142 frames per window. The hop in samples must advance by
    # exactly 142 frames, or events drift apart on long songs.
    window, margin, keep = 43844, 15, 142
    stride = keep * HOP
    padded = np.pad(audio, (margin * HOP, window))
    total = (len(audio) + stride - 1) // stride
    session = _session()
    times = (len(audio) + HOP - 1) // HOP
    strength = np.empty(total * keep, dtype=np.float32)
    pitch = np.empty(total * keep, dtype=np.int16)
    activity = np.empty((times, 88), dtype=np.uint8)
    last = -1
    for i in range(total):
        percent = i * 10 // total
        if percent != last:
            progress(f"Analisando a melodia com IA · {percent * 10}%")
            last = percent
        chunk = padded[i * stride:i * stride + window].reshape(1, window, 1)
        notes, onsets = session.run(
            ["StatefulPartitionedCall:1", "StatefulPartitionedCall:2"],
            {"serving_default_input_2:0": chunk},
        )
        # Favor predicted starts that lead to audible pitched notes.
        activation = notes[0]
        future = np.maximum(activation, activation[np.minimum(np.arange(len(activation)) + 3, len(activation)-1)])
        weighted = (onsets[0] * np.sqrt(np.maximum(0, future)))[margin:margin + keep]
        best = weighted.argmax(axis=1)
        block = slice(i * keep, (i + 1) * keep)
        strength[block] = weighted[np.arange(keep), best]
        pitch[block] = best
        left = i * keep
        size = min(keep, times-left)
        activity[left:left + size] = np.rint(255 * np.clip(activation[margin:margin + size], 0, 1))
    return strength[:times], pitch[:times], activity


def _percussion(audio):
    size = 1024
    padded = np.pad(audio, (size // 2, size // 2))
    frames = np.lib.stride_tricks.sliding_window_view(padded, size)[::HOP]
    n = (len(audio) + HOP - 1) // HOP
    window = np.hanning(size).astype(np.float32)
    flux = np.zeros((n, 3), dtype=np.float32)
    rms = np.empty(n, dtype=np.float32)
    previous = np.zeros(size // 2 + 1, dtype=np.float32)
    # Divide into bass, mids and treble; scale each band's novelty later so
    # quiet snares and hats survive bass-heavy mastering.
    bands = [(1, 9), (9, 94), (94, size // 2 + 1)]
    for start in range(0, n, 512):
        batch = frames[start:min(n, start + 512)]
        spec = np.log1p(np.abs(np.fft.rfft(batch * window)) * 2)
        difference = np.maximum(0, np.diff(spec, axis=0, prepend=previous[None, :]))
        for b, (low, high) in enumerate(bands):
            flux[start:start + len(batch), b] = difference[:, low:high].mean(axis=1)
        rms[start:start + len(batch)] = np.sqrt((batch * batch).mean(axis=1))
        previous = spec[-1]
    scale = np.percentile(flux, 93, axis=0)
    flux = np.clip(flux / np.maximum(scale, [0.015, 0.009, 0.004]), 0, 5)
    return flux, rms


def _period(curve, prior=None):
    # Autocorrelation picks a tempo, not a start time. Compare several lags of
    # the same period so a snare on beats two and four also supports the pulse.
    x = np.maximum(0, curve - np.mean(curve))
    size = 1 << (2 * len(x) - 1).bit_length()
    spec = np.fft.rfft(x, size)
    ac = np.fft.irfft(spec * spec.conj(), size)
    lags = np.arange(round(FPS * 60 / 184), min(round(FPS * 60 / 62), len(x) // 4))
    if len(lags) < 3 or ac[0] < 1e-6:
        return prior or FPS * 0.5, 0.0
    base = (ac[lags] + .6 * ac[lags * 2] + .3 * ac[lags * 3]) / ac[0]
    preferred = prior or FPS * 60 / 112
    # Favor the established metrical level. Strong evidence can switch tempos.
    weight = 0.73 + .27 * np.exp(-.5 * (np.log2(lags / preferred) / .29) ** 2)
    scores = base * weight
    j = scores.argmax()
    lag = float(lags[j])
    if 0 < j < len(scores) - 1:
        a, b, c = scores[j-1:j+2]
        if a - 2*b + c < -1e-8:
            lag += float(np.clip(.5 * (a-c) / (a-2*b+c), -.5, .5))
    return lag, float(base[j])


def _phrase_scale(values, quantile=90, floor=.35):
    span = round(4 * FPS)
    offsets = np.arange(0, len(values) + span, span)
    scale = [max(floor, np.percentile(values[max(0, j-span):min(len(values), j+span)], quantile))
             if j-span < len(values) else floor for j in offsets]
    return np.interp(np.arange(len(values)), offsets, scale)


def _beat_track(drum, pitched):
    # Solve for a *sequence* of attacks with a consistent local period. A vocal
    # syllable between drum hits cannot independently reset the phase of a bar.
    rhythm = np.clip(drum, 0, 4) + .12 * np.clip(pitched, 0, 2)
    nominal, _ = _period(rhythm)
    step = round(3 * FPS)
    centers = np.arange(0, len(rhythm) + step, step)
    periods, strengths = [], []
    for c in centers:
        around = rhythm[max(0, c-2*step):min(len(rhythm), c+2*step)]
        measured, coherence = _period(around, nominal)
        periods.append(measured if coherence > .21 else nominal)
        strengths.append(coherence)
    frames = np.arange(len(rhythm))
    period = np.interp(frames, centers, periods)
    # Mild smoothing couples adjacent flux samples from the same drum hit.
    energy = np.convolve(rhythm, [.18, .64, .18], mode="same")
    reward = np.clip(energy / _phrase_scale(energy), 0, 2.5)
    dp = np.zeros(len(rhythm))
    prev = np.full(len(rhythm), -1, dtype=int)
    transitions = {}
    for j in range(len(dp)):
        # Integer lookup caches the transition cost for the local quarter-frame tempo.
        bucket = round(4 * period[j])
        if bucket not in transitions:
            length = bucket / 4
            hops = np.arange(max(1, round(.80*length)), round(1.22*length)+1)
            transitions[bucket] = (hops, 115 * np.log(hops / length)**2)
        hops, cost = transitions[bucket]
        if j >= hops[-1]:
            path = dp[j-hops] - cost
            best = int(path.argmax())
            prev[j] = j - hops[best]
            dp[j] = path[best]
        elif j >= hops[0]:
            available = hops <= j
            path = dp[j-hops[available]] - cost[available]
            best = int(path.argmax())
            prev[j] = j-hops[best]
            dp[j] = path[best]
        dp[j] += reward[j]
    end = max(0, len(dp)-int(period[-1]*1.15))
    j = end + int(np.argmax(dp[end:]))
    beats = []
    while j >= 0:
        beats.append(j)
        j = prev[j]
    beats = np.array(beats[::-1], dtype=int)
    confidence = np.interp(beats, centers, strengths)
    return round(60 * FPS / nominal, 1), beats, confidence


def _peaks(curve, gate):
    previous = np.r_[curve[0], curve[:-1]]
    following = np.r_[curve[1:], curve[-1]]
    # The initial frame may already contain the attack; the game has a count-in.
    return np.flatnonzero((curve >= previous) & (curve > following) & (curve > gate))


def _select(candidates, ranking, distance, inherited=()):
    selected = sorted(inherited)
    for pos in candidates[np.argsort(-ranking[candidates], kind="stable")]:
        pos = int(pos)
        i = bisect_left(selected, pos)
        if i and pos - selected[i - 1] < distance:
            continue
        if i < len(selected) and selected[i] - pos < distance:
            continue
        selected.insert(i, pos)
    return selected


def _charts(flux, rms, melody, pitches, activity):
    bass, mid, high = flux.T
    drum = np.maximum.reduce([bass, mid * .86, high * .48])
    avg = np.convolve(melody, np.ones(17)/17, mode="same")
    pitched = np.maximum(0, melody - avg * .76)
    pitch_rank = np.minimum(pitched / max(.14, np.percentile(pitched,94)), 4)
    bpm, grid, coherence = _beat_track(drum, pitch_rank)
    drum_rank = drum / _phrase_scale(drum)
    loud = rms > max(.00015, np.percentile(rms,92) * .018)
    perc = _peaks(drum, max(.19, np.percentile(drum,55) * .80))
    tonal = _peaks(pitched, max(.055, np.percentile(pitched,65) * .8))
    candidates = np.union1d(perc, tonal)
    candidates = candidates[loud[candidates]]
    if not len(candidates):
        raise ValueError("Não encontrei ataques musicais suficientes nesta gravação.")
    # Mark each salient onset once. Prefer percussion on main beats, with
    # melodic starts admitted for sparse/acoustic passages and ornamentation.
    perc_strength = np.zeros(len(rms))
    perc_strength[perc] = drum_rank[perc]
    tonal_strength = np.zeros(len(rms))
    tonal_strength[tonal] = pitch_rank[tonal]
    strength = np.maximum(perc_strength, tonal_strength * .8)
    support = np.zeros(len(rms), dtype=bool)
    local_span = round(3 * FPS)
    for offset in range(0,len(rms),local_span):
        a = bisect_left(grid, max(0,offset-local_span//2))
        b = bisect_left(grid, offset+local_span*1.5)
        inside = grid[a:b]
        if not len(inside):
            continue
        sounding = loud[inside]
        anchored = (drum_rank[inside] >= .43) | (pitch_rank[inside] >= .55)
        if sounding.mean() >= .35 and np.mean(anchored & sounding) >= .39 and np.median(coherence[a:b]) >= .16:
            support[offset:offset+local_span] = True
    period = FPS * 60 / bpm
    main, halves, quarters = [], [], []
    # An attack may sit just ahead of the ideal time (swing, playing feel).
    # Keep its recorded time, never pull a sung syllable onto an empty beat.
    def pick(target, radius, emphasize_drum, threshold):
        a = candidates.searchsorted(target-radius)
        b = candidates.searchsorted(target+radius, side="right")
        q = candidates[a:b]
        if not len(q):
            return None
        accent = np.maximum(perc_strength[q], tonal_strength[q]*emphasize_drum)
        score = accent / (1 + 1.6*((q-target)/max(1, radius))**2)
        k = int(np.argmax(score))
        return int(q[k]) if accent[k] >= threshold else None
    for i, t in enumerate(grid):
        if not support[t]:
            continue
        width = grid[i+1]-t if i+1<len(grid) else period
        hit = pick(t, min(4.5,width*.13), .34, .38)
        if hit is not None:
            main.append(hit)
        if i+1 == len(grid) or not support[grid[i+1]]:
            continue
        hit = pick(t+width*.5, min(4,width*.11), .70, .71)
        if hit is not None:
            halves.append(hit)
        for frac in (.25,.75):
            hit = pick(t+width*frac,min(3,width*.08), .95, .95)
            if hit is not None:
                quarters.append(hit)
    # Sections without a reliable beat follow only distinct audible onsets.
    # This also handles recordings that consist entirely of free-tempo melody.
    free = candidates[~support[candidates]]
    fallback = free[strength[free] >= .65]
    easy = _select(fallback, strength, max(.36*FPS,.79*period), main)
    if not easy:
        easy = _select(candidates, strength, max(.38*FPS,.82*period))
    medium = _select(np.array(halves,dtype=int), strength, .17*FPS, easy)
    medium = _select(free[strength[free]>=.70], strength, max(.22*FPS,.43*period), medium)
    hard = _select(np.array(quarters,dtype=int), strength, .095*FPS, medium)
    hard = _select(free[strength[free]>=.60], strength, max(.13*FPS,.25*period), hard)
    # Strong non-grid melodic accents are eligible in hard, including pickups.
    # Ignore crowded voiced syllables, which would obscure the recurrent beat.
    embellishments = tonal[(tonal_strength[tonal]>=1.65) & loud[tonal]]
    hard = _select(embellishments, strength, max(.13*FPS,.27*period),hard)
    shared = set()
    for a in range(0,len(rms),max(1,round(16*period))):
        b = a+round(16*period)
        group = easy[bisect_left(easy,a):bisect_left(easy,b)]
        if group:
            shared.add(max(group,key=lambda j:strength[j]))
    voiced = pitches[tonal]
    low, high_pitch = np.percentile(voiced,[12,88]) if len(voiced) else (15,55)
    high_pitch = max(low+12, high_pitch)
    charts = {}
    for name, positions, lanes in [("easy",easy,3),("medium",medium,4),("hard",hard,5)]:
        chart = []
        # Stable lane grammar: bass/kick low, snare/clap middle, hats high.
        # Tonal notes in melody-led phrases follow register instead.
        for j in positions:
            bands = flux[j] * [1, .92, .70]
            if tonal_strength[j] > max(.6, perc_strength[j]*1.35):
                register = np.clip((pitches[j]-low)/(high_pitch-low),0,1)
            else:
                register = (0, .53, 1)[int(np.argmax(bands))]
                if name!="easy" and min(bands[0],bands[1]) > .83 * max(bands):
                    register = .30
            chart.append({"t":round(j/FPS,4),"lane":round(float(register)*(lanes-1)),"sync":j in shared})
        _holds(chart, positions, tonal, melody, pitches, activity, name)
        charts[name]=chart
    # The rail displays bar marks only where both periodicity and onsets agree.
    beat_times = [round(int(j)/FPS,4) for j in grid if support[j] and loud[j]]
    return bpm, charts, beat_times


def _holds(chart, positions, tonal, melody, pitches, activity, difficulty):
    # Follow the *same predicted pitch* across frames. A short release tail,
    # transient drum or hat cannot become a hold purely from event spacing.
    next_on_lane = {}
    possible = {}
    for i in reversed(range(len(chart))):
        j, lane = positions[i], chart[i]["lane"]
        limit = min(j+round(2.8*FPS),next_on_lane.get(lane,len(activity))-round(.14*FPS),len(activity)-1)
        next_on_lane[lane] = j
        if chart[i]["sync"] or limit-j < .55*FPS:
            continue
        near = tonal[(tonal >= j-3) & (tonal <= j+3)]
        if not len(near):
            continue
        onset = near[np.argmax(melody[near])]
        if melody[onset] < .14:
            continue
        strength = activity[j:limit+1,pitches[onset]]
        gate = max(37, int(np.max(strength[:9]))*.35)
        sustained = strength >= gate
        short_gaps = np.convolve((~sustained).astype(np.int8),np.ones(4,dtype=np.int8),mode="valid")
        gaps = np.flatnonzero(short_gaps>=4)
        length = int(gaps[0]) if len(gaps) else len(sustained)-1
        if length >= .55*FPS and np.mean(sustained[:length]) >= .90:
            possible[i]=length
    spacing = {"easy":9, "medium":6, "hard":4}[difficulty]*FPS
    last = -spacing
    for i,j in enumerate(positions):
        if i in possible and j >= last+spacing:
            chart[i]["duration"]=round(possible[i]/FPS,4)
            last=j


def analyze(path: Path, progress=None) -> dict:
    """Return ready-to-play charts. Times are audio-relative seconds."""
    notify = progress or (lambda _: None)
    notify("Lendo o áudio")
    audio = _decode(path)
    notify("Identificando ataques e percussão")
    flux, rms = _percussion(audio)
    melodic, pitch, activity = _melody(audio, notify)
    notify("Criando mapas de 3, 4 e 5 pistas")
    bpm, charts, beats = _charts(flux, rms, melodic, pitch, activity)
    waveform = [round(float(block.max()), 3) for block in np.array_split(rms, min(96, len(rms)))]
    level = max(max(waveform), 0.001)
    return {"duration": round(len(audio) / SAMPLE_RATE, 4), "bpm": bpm, "engine": ENGINE,
            "analysisVersion": VERSION, "charts": charts, "beats": beats,
            "waveform": [round(value / level, 3) for value in waveform]}
