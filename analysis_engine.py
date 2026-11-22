"""Music transcription and percussion analysis for Pulso's local rhythm charts."""
from __future__ import annotations

import os
import subprocess
from bisect import bisect_left
from functools import lru_cache
from pathlib import Path

import numpy as np
import onnxruntime as ort
from instrumental import accompaniment, articulation_features, attack_features, bass_resonance, repeating_tones, separate_instruments

SAMPLE_RATE = 22050
HOP = 256
FPS = SAMPLE_RATE / HOP
MODEL = Path(__file__).parent / "models" / "basic-pitch" / "nmp.onnx"
ENGINE = "Pulso 9 · frases da gravação"
VERSION = 9


def _decode(path, sample_rate=SAMPLE_RATE, side=False):
    spatial = ["-af", "aformat=channel_layouts=stereo,pan=mono|c0=0.5*c0-0.5*c1"] if side else []
    command = [
        os.environ.get("FFMPEG_PATH") or "ffmpeg", "-nostdin", "-v", "error",
        "-protocol_whitelist", "file,pipe", "-i", str(Path(path).resolve()),
        "-map", "0:a:0", "-vn", "-sn", "-dn", *spatial, "-ac", "1", "-ar", str(sample_rate),
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
    if len(audio) < sample_rate or not np.isfinite(audio).all():
        raise ValueError("A música precisa conter pelo menos um segundo de áudio válido.")
    if not side and sample_rate == SAMPLE_RATE and float(np.max(np.abs(audio))) < 0.0001:
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
    # Keep a bass and an upper-register hypothesis independently. A sustained
    # bass and the lead can start together or alternate inside the same phrase.
    strength = np.empty((times, 2), dtype=np.float32)
    pitch = np.empty((times, 2), dtype=np.int16)
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
        left = i * keep
        size = min(keep, times-left)
        block = slice(left, left + size)
        for part, (lo, hi) in enumerate(((0, 32), (32, 88))):
            best = lo + weighted[:size, lo:hi].argmax(axis=1)
            strength[block, part] = weighted[np.arange(size), best]
            pitch[block, part] = best
        activity[left:left + size] = np.rint(255 * np.clip(activation[margin:margin + size], 0, 1))
    return strength, pitch, activity


def _instrument_features(path, progress, mix=None, featured=None):
    if mix is None:
        mix = _decode(path)
    size = len(mix)
    count = (size + HOP - 1) // HOP
    window = np.lib.stride_tricks.sliding_window_view(np.pad(mix, 512), 1024)[::HOP]
    rms = np.empty(count, dtype=np.float32)
    for start in range(0, count, 512):
        block = window[start:min(count, start + 512)]
        rms[start:start+len(block)] = np.sqrt((block * block).mean(axis=1))
    low_body = bass_resonance(mix, SAMPLE_RATE, HOP)
    repeats = repeating_tones(mix, SAMPLE_RATE, HOP)
    del window, mix
    spatial = _decode(path, side=True)
    if len(spatial)!=size:
        spatial=np.pad(spatial[:size],(0,max(0,size-len(spatial))))
    side_flux,side_level = attack_features(spatial,SAMPLE_RATE,HOP)
    side_art,side_tones = articulation_features(spatial,SAMPLE_RATE,HOP)
    del spatial
    progress("Isolando os instrumentos")
    vocal = {}
    if featured is None:
        stems = separate_instruments(path,size,progress,vocal_cues=vocal)
    else:
        images = separate_instruments(path,size,progress,stereo=True,include_voice=True,vocal_cues=vocal)
        featured.update(bass=images[1],drums=images[2],voice=images[3])
        stems = images[:3].mean(axis=1)
    features = {"duration": size / SAMPLE_RATE, "rms": rms, "low_body": low_body, "repeats": repeats,
                "side_flux":side_flux,"side_level":side_level,"side_art":side_art,"side_tones":side_tones,**vocal}
    if featured is None:
        companion=accompaniment(path,size,progress)
    else:
        featured["lead"]=accompaniment(path,size,progress,stereo=True)
        companion=featured["lead"].mean(axis=0)
    features["comp_flux"], features["comp_level"] = attack_features(companion,SAMPLE_RATE,HOP)
    features["comp_art"], features["comp_tones"] = articulation_features(companion,SAMPLE_RATE,HOP)
    del companion
    for name, samples in zip(("lead", "bass", "drums"), stems):
        features[name + "_flux"], features[name + "_level"] = attack_features(samples, SAMPLE_RATE, HOP)
        if name != "drums":
            features[name+"_art"], features[name+"_tones"] = articulation_features(samples, SAMPLE_RATE, HOP)
            label = "a linha instrumental" if name == "lead" else "o baixo"
            strength, pitch, activity = _melody(samples, lambda msg: progress("Transcrevendo " + label + " · " + msg.split(" · ")[-1]))
            features.update({name + "_strength": strength, name + "_pitch": pitch, name + "_activity": activity})
    return features


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


def _phrase_heads(candidates, ranking, distance, repicks=None):
    """Choose the strongest playable sequence, considering both sides of each attack."""
    if not len(candidates):
        return []
    predecessor = np.searchsorted(candidates, candidates-int(np.ceil(distance)), side="right")
    reward = np.log1p(ranking[candidates])
    total = np.zeros(len(candidates)+1)
    ending = np.empty(len(candidates))
    last = np.full(len(candidates)+1,-1,dtype=int)
    link = np.full(len(candidates),-1,dtype=int)
    if repicks is not None:
        envelope, eligible=repicks
    short = np.searchsorted(candidates,candidates-round(.070*FPS),side="right") if repicks is not None else predecessor
    for i, previous in enumerate(predecessor):
        head=candidates[i]
        value, link[i] = total[previous],last[previous]
        # Two clear picks with a genuine dip between them can be closer than
        # ordinary taps. A lone loud transient cannot enable this exception.
        if repicks is not None and eligible[head] and envelope[head]>.52:
            for k in range(previous,short[i]):
                earlier=candidates[k]
                floor=min(envelope[earlier],envelope[head])
                if eligible[earlier] and floor>.52 and ending[k]>value and envelope[earlier+1:head].min()<.67*floor:
                    value,link[i]=ending[k],k
        ending[i]=value+reward[i]
        if ending[i]>total[i]+1.e-9:
            total[i+1],last[i+1]=ending[i],i
        else:
            total[i+1],last[i+1]=total[i],last[i]
    selected = []
    i=last[-1]
    while i>=0:
        selected.append(int(candidates[i]))
        i=link[i]
    return selected[::-1]


def _tonal_events(melody, pitches, activity):
    """Reject envelope ripples unless a pitched sound really starts here."""
    peaks = _peaks(melody, .13)
    pitch = pitches[peaks]

    def energy(offset):
        frames = np.clip(peaks + offset, 0, len(activity) - 1)
        return activity[frames, pitch].astype(np.float32) / 255

    before = (energy(-6) + energy(-4) + energy(-2)) / 3
    after = (energy(2) + energy(4) + energy(6) + energy(9)) / 4
    rise = np.clip((after - before) / .30, 0, 1)
    certainty = np.zeros(len(activity))
    certainty[peaks] = melody[peaks] * (.38 + .62 * np.sqrt(after)) * (.48 + .52 * rise)
    return np.asarray(_select(peaks, certainty, .095 * FPS), dtype=int), certainty


def _pitch_register(times, pitch, certainty, period, nframes):
    """Express pitch in the current phrase without losing the song's register.

    A full-song min/max squeezes many distinct neighboring pitches into one
    key when the recording spans several octaves. Midranks keep two recurring
    notes distinct and still give the same pitch one stable place in a phrase.
    """
    result = np.full(nframes, .5)
    if not len(times):
        return result
    def midranks(notes, weights):
        # Square root keeps a repeated ostinato from hiding its less frequent pickup.
        hist = np.sqrt(np.bincount(notes, weights=weights, minlength=88))
        return (np.cumsum(hist) - hist*.5) / max(1e-8, hist.sum())
    global_scale = midranks(pitch, certainty)
    span = max(1, round(period*4))
    centers = np.arange(0, nframes + span, span)
    palettes = []
    for center in centers:
        lo, hi = np.searchsorted(times, [center-span, center+span])
        if hi-lo >= 3:
            palettes.append(.8*midranks(pitch[lo:hi], certainty[lo:hi]) + .2*global_scale)
        else:
            palettes.append(global_scale)
    palettes = np.array(palettes)
    segment = times // span
    alpha = (times % span) / span
    result[times] = ((1-alpha) * palettes[segment, pitch]
                     + alpha * palettes[np.minimum(segment+1, len(centers)-1), pitch])
    return result


def _source_presence(level, rms, mix_reference):
    frames = np.arange(len(level))
    future = np.maximum.reduce([level[np.minimum(frames+k, len(level)-1)] for k in (0, 2, 4)])
    reference = np.maximum.reduce((.56*rms, .28*mix_reference,
                                   np.full(len(level), max(.0005, np.percentile(rms, 90)*.028))))
    return np.clip((future/reference - .10) / .34, 0, 1)


def _riff_voicings(data, times, pitches, seconds, presence):
    """Resolve notes sharing a pick and keep recurring string shapes on the same keys."""
    if "lead_tones" not in data:
        return
    tones = data["lead_tones"]
    activity = data["lead_activity"]
    history = []
    for j in times:
        if presence[j]<.55:
            continue
        stop=min(len(tones),j+7)
        spectrum=tones[j:stop].mean(axis=0)
        if "side_tones" in data:
            width=min(.45,float(data["side_level"][j])/max(.004,data["rms"][j]))
            side=data["side_tones"][j:stop].mean(axis=0)
            spectrum = spectrum + width*side
        # Local prominence separates fretted pitches from the continuous
        # distortion/noise floor. An octave overtone alone is not a chord.
        pad=np.pad(spectrum,(3,3),mode="edge")
        body=np.maximum(0,spectrum-.53*(pad[:-6]+pad[6:])*.5)
        body/=max(.001,float(body.max()))
        sounding=activity[j:stop,7:64].mean(axis=0)
        strength=body*(.35+.65*np.minimum(1,sounding/84))
        peaks=_peaks(strength,.18)
        if not len(peaks):
            continue
        root=int(peaks[strength[peaks].argmax()])
        model=pitches[j]-7
        if 0<=model<len(body) and strength[model]>=.86*strength[root]:
            root=model
        uncertain = not (0<=model<len(body)) or (sounding[model]<86 and strength[model]<.85*strength[root])
        if uncertain and sounding[root]>=30:
            pitches[j]=root+7
        primary=pitches[j]-7
        partner=seconds[j]
        if 0<=primary<len(body) and partner<0 and strength[primary]>=.34:
            neighbours=peaks[(abs(peaks-primary)>=3)&(abs(peaks-primary)<=9)]
            if len(neighbours):
                q=int(neighbours[strength[neighbours].argmax()])
                if strength[q]>=max(.29,.68*strength[primary]) and min(sounding[primary],sounding[q])>=33:
                    pitches[j],partner=sorted((primary+7,q+7))
        signature=body/(np.linalg.norm(body)+1.e-6)
        history=[h for h in history if j-h[0]<1.4*FPS]
        if history:
            sim=np.array([h[1]@signature for h in history])
            k=int(sim.argmax())
            _,_,old,other=history[k]
            if sim[k]>=.966 and abs(old-pitches[j])<=2:
                pitches[j]=old
                if partner<0 and other>=0 and sounding[other-7]>=35:
                    partner=other
        seconds[j]=partner if partner>=0 and abs(partner-pitches[j])>=3 else -1
        if 7<=pitches[j]<64 and (partner<0 or 7<=partner<64):
            history.append((j,signature,pitches[j],partner))


def _pitch_attacks(tones):
    """Find a new fretted/string attack even when the total loudness stays flat."""
    count=len(tones)
    reference=np.maximum(.0009,.07*np.percentile(tones,90,axis=0))
    body=np.log1p(tones/reference)
    padded=np.pad(body,((4,4),(0,0)),mode="edge")
    change=np.maximum(0,.56*(padded[5:5+count]-padded[3:3+count])+
                         .44*(padded[6:6+count]-padded[1:1+count]))
    change*=np.sqrt(np.clip(tones/(reference*4.5),0,1))
    curve=np.partition(change,-5,axis=1)[:,-5:].mean(axis=1)
    return curve/_phrase_scale(curve,90,max(.075,.16*np.percentile(curve,90)))


def _instrument_line(data, part, presence):
    """Use the separated pick attack for time and the following pitched body for the key."""
    flux, activity = data[part + "_flux"], data[part + "_activity"]
    onset, notes = data[part + "_strength"], data[part + "_pitch"]
    weights = [.08, .37, .37, .16, .02] if part == "lead" else [.77, .19, .04, 0, 0]
    raw = flux @ weights
    if part == "lead":
        # A pluck's playable attack is the new spectrum at the end of the
        # two-hop difference; its midpoint systematically leads picked strings.
        raw = np.r_[0., raw[:-1]]
    novelty = raw / _phrase_scale(raw, 90, max(.032, np.percentile(raw, 90)*.20))
    # A muted re-pick can keep exactly the same pitch. Listen to the new
    # energy across the instrument's octaves, including below the vocal range.
    attack = np.zeros(len(raw))
    if part+"_art" in data:
        bands = data[part+"_art"]
        balance = [.02,.04,.16,.23,.25,.2,.09,.01] if part=="lead" else [.48,.36,.13,.03,0,0,0,0]
        envelope = bands @ balance
        attack = envelope/_phrase_scale(envelope,89,max(.045,.16*np.percentile(envelope,90)))
        pitched_attack = _pitch_attacks(data[part+"_tones"])
        novelty = .39*novelty + .36*attack + .25*pitched_attack
        if part=="lead" and "side_art" in data:
            # A wide guitar or string section can cancel in the mono analysis.
            # Its stereo attacks only contribute while a separated instrument is audible.
            side = data["side_art"] @ [.01,.01,.10,.24,.29,.23,.10,.02]
            edge = data["side_flux"] @ [.02,.20,.54,.22,.02]
            spatial = .25 * side/_phrase_scale(side,89,max(.05,.18*np.percentile(side,90)))
            spatial += .23 * edge/_phrase_scale(edge,89,max(.03,.18*np.percentile(edge,90)))
            spatial += .52 * _pitch_attacks(data["side_tones"])
            width = np.clip((data["side_level"]/np.maximum(.004,data["rms"])-.08)/.31,0,1)*presence
            novelty = (1-.78*width)*novelty + .78*width*spatial
        if part=="lead" and "comp_art" in data:
            # The two separators disagree most on distorted guitar and on strings
            # behind a singer. The accompaniment specialist times the attack;
            # the first estimate and stereo body still describe the chord shape.
            comp_art=data["comp_art"] @ [.01,.03,.13,.23,.26,.23,.10,.01]
            comp_flux=data["comp_flux"] @ [.03,.26,.44,.24,.03]
            comp=.28*comp_art/_phrase_scale(comp_art,89,max(.045,.14*np.percentile(comp_art,90)))
            comp+=.25*comp_flux/_phrase_scale(comp_flux,89,max(.030,.14*np.percentile(comp_flux,90)))
            comp+=.47*_pitch_attacks(data["comp_tones"])
            ratio=data["comp_level"]/np.maximum(.003,.65*data["rms"]+.16*_phrase_scale(data["rms"],85,.001))
            share=.90*np.clip((ratio-.035)/.16,0,1)
            novelty=(1-share)*novelty+share*comp
    audible = novelty * presence
    acoustic = _peaks(audible, .25)
    acoustic = np.array(_select(acoustic, audible, .047*FPS), dtype=int)
    score = np.zeros(len(raw))
    score[acoustic] = np.minimum(2.65, novelty[acoustic]) * presence[acoustic]
    model_strength = np.zeros(len(raw))
    model_pitch = np.full(len(raw), -1, dtype=int)
    for i in range(2):
        starts, confidence = _tonal_events(onset[:, i], notes[:, i], activity)
        for j in starts[confidence[starts] > .13]:
            candidates = acoustic[np.searchsorted(acoustic, j-5):np.searchsorted(acoustic, j+5)]
            k = candidates[np.argmin(abs(candidates-j))] if len(candidates) else j
            value = confidence[j] * presence[k]
            if value > model_strength[k]:
                model_strength[k], model_pitch[k] = value, notes[j, i]
            # A quiet legato pitch change is playable even without a sharp pick attack.
            score[k] = max(score[k], min(2., value*3.4))
    raw_pitch = np.full(len(raw), -1, dtype=int)
    second = np.full(len(raw), -1, dtype=int)
    last_pitch = -1
    last_shape, last_strum = None, -1000
    level = data[part + "_level"]
    if part=="lead" and "comp_level" in data:
        level=.40*level+.60*data["comp_level"]
    peak_level = _phrase_scale(level, 80, max(.001, .09*np.percentile(level, 90)))
    for j in np.flatnonzero(score >= .25):
        body = activity[j:min(len(raw), j+8)].max(axis=0).astype(float)
        steady = activity[j:min(len(raw), j+8)].mean(axis=0)
        prior = activity[max(0, j-7):max(1, j-2)].mean(axis=0)
        rise = np.maximum(0, body-prior)
        likelihood = .50 * body + .50 * rise
        if model_pitch[j] >= 0:
            likelihood[model_pitch[j]] += min(70, model_strength[j]*135)
        p = int(likelihood.argmax())
        if part == "lead" and novelty[j] >= .74 and body[p] >= 74:
            # Identify a voiced pair once, then recognise audible re-strums of
            # that same shape even if both strings are still vibrating.
            others = np.flatnonzero((steady >= max(56, steady[p]*.57)) & (rise >= 21))
            others = [q for q in others if 3 <= abs(q-p) <= 19 and abs(q-p) % 12 in (3,4,5,7,8,9,10)]
            if others and rise[p] >= 20 and steady[p] >= 68:
                last_shape = sorted((p, max(others,key=lambda q: likelihood[q])))
                last_strum = j
            elif (last_shape is not None and j-last_strum <= .72*FPS and
                  p in last_shape and min(steady[last_shape]) >= max(49,.51*steady[p])):
                last_strum = j
            if last_strum == j:
                p, second[j] = last_shape
        if body[p] < 56 and last_pitch >= 0:
            p = last_pitch  # Muted re-picks stay on the current string shape.
        raw_pitch[j] = last_pitch = p
        # Releasing/fretting a string makes a small acoustic transient. Only
        # keep it as a pick if there is pitched or level evidence after it.
        before_level = level[max(0,j-6):max(1,j-2)].mean()
        after_level = level[j:min(j+7,len(raw))].mean()
        pick = np.clip((after_level-.67*before_level)/max(.0005,.38*peak_level[j]),0,1)
        pitched = np.clip(max(rise[p]/85, model_strength[j]/.29),0,1)
        score[j] *= .38 + .62*max(pick, pitched, .82*min(1.,novelty[j]))
    score *= 1 + .16 * np.minimum(1, model_strength/.38)
    chosen = np.asarray(_select(np.flatnonzero(score >= .30), score, .050*FPS), dtype=int)
    if part=="lead":
        _riff_voicings(data,chosen,raw_pitch,second,presence)
    selected = np.zeros_like(score)
    selected[chosen] = score[chosen]
    return selected, raw_pitch, second, audible


def _attack_offsets(curve):
    """Interpolate the local attack peak between analysis hops, on the recording clock."""
    before=np.r_[curve[0],curve[:-1]]
    after=np.r_[curve[1:],curve[-1]]
    peak=(curve>before)&(curve>=after)
    offsets=np.zeros(len(curve))
    offsets[peak]=np.clip(.5*(after[peak]-before[peak])/
                          np.maximum(1.e-7,2*curve[peak]-before[peak]-after[peak]),-.46,.46)
    return offsets


def _head_times(data, positions, parts, offsets):
    """Keep a shared audio clock for every difficulty and refine exposed picking."""
    indices=np.asarray(positions,dtype=int)
    times=indices.astype(float)+np.choose(parts[indices], [v[indices] for v in offsets])
    # The longer windows distinguish notes under distortion, but they start
    # responding before a clean string actually sounds. Find the short-window
    # pick on the same separated instrument when the kit leaves it exposed.
    detail=data["lead_flux"] @ [.04,.26,.37,.28,.05]
    pick=np.r_[0.,detail[:-1]]
    subframe=_attack_offsets(pick)
    strength=pick/_phrase_scale(pick,90,.035)
    mix=np.maximum(.001,data["rms"])
    span=min(len(mix),max(1,round(.72*FPS)))
    window=np.ones(span)/span
    lead_level=data["lead_level"]
    kit_level=data["drums_level"]
    quiet_kit=np.convolve(kit_level-.18*mix,window,mode="same")<0
    foreground=np.convolve(lead_level-.38*mix,window,mode="same")>0
    exposed=quiet_kit & foreground & (lead_level>.32*mix) & (kit_level<.24*mix)
    for i in np.flatnonzero((parts[indices]==0)&exposed[indices]):
        j=indices[i]
        lo,hi=max(0,j-1),min(len(pick),j+5)
        k=lo+int(np.argmax(pick[lo:hi]))
        if strength[k]>.24:
            times[i]=k+subframe[k]
    # The same hard-map boundaries protect rapid repeats in all three maps.
    room=np.maximum(0,(np.diff(indices)-.060*FPS)/2)
    times=np.clip(times,indices-np.r_[4.,room],indices+np.r_[room,4.])
    return {int(j):round(float(np.clip(t/FPS,0,data["duration"])),4) for j,t in zip(indices,times)}


def _ringing_bass(data, kit_hits):
    # A low sustained sound may be split between the bass and accompaniment
    # estimates. Require its physical fundamental in the mix and a stable pitch
    # in a separated instrumental source before giving a coincident strike a hold.
    body, rms = data["low_body"], data["rms"]
    result = {}
    last_end = -1
    for j in kit_hits:
        if j+42 >= len(rms):
            break
        if j < last_end:
            continue
        before = slice(max(0,j-14), max(1,j-5))
        after = slice(j+12,j+34)
        sounding = np.zeros(25)
        for name in ("bass","lead"):
            reference = data[name+"_activity"][:,7:32]
            ratio = data[name+"_level"][after].mean()/max(.002,rms[after].mean())
            if ratio >= .12:
                stable = reference[after].mean(axis=0)
                change = stable-reference[before].mean(axis=0)
                sounding = np.maximum(sounding, np.where((stable>=126)&(change>=44),stable,0))
        loud = np.median(body[after], axis=0)
        previous = np.median(body[before], axis=0)
        rises = (loud > 1.65*previous+.003) & (loud > .23*max(.004,rms[after].mean()))
        strength = sounding * loud * rises
        if not strength.any():
            continue
        pitch = int(strength.argmax())
        end = min(len(rms)-1, j+round(2.6*FPS))
        quiet = body[j+29:end+1,pitch] < loud[pitch]*.54
        gaps = np.flatnonzero(np.convolve(quiet,np.ones(4,int),mode="valid")==4)
        if len(gaps):
            end = j+29+int(gaps[0])
        if end-j >= .46*FPS:
            result[int(j)] = (pitch+7, end)
            last_end = end
    return result


def _groove_weights(line, phase, period):
    """Recognise a played subdivision locally, including displaced and triplet patterns."""
    frames=np.arange(len(line))
    half=max(12,round(2.2*period))
    lo,hi=np.maximum(0,frames-half),np.minimum(len(line),frames+half+1)
    def local(values):
        prefix=np.r_[0,np.cumsum(values)]
        return prefix[hi]-prefix[lo]
    mass=np.sqrt(np.clip(line,0,2.5))
    total=local(mass)
    confidence=np.zeros(len(line))
    fit=np.ones(len(line))
    for division in (1,2,3,4):
        angle=2*np.pi*division*phase
        circular=local(mass*np.exp(1j*angle))
        strength=np.abs(circular)/np.maximum(.001,total)
        # A few isolated notes can agree with any grid. Require multiple
        # observations in the phrase and favour the simplest supported rhythm.
        strength*=np.clip((total-2.7)/5,0,1)*(1-.025*(division-1))
        use=strength>confidence
        phase_error=1-np.cos(angle-np.angle(circular))
        fit[use]=np.exp(-1.45*phase_error[use])
        confidence[use]=strength[use]
    trust=np.clip((confidence-.22)/.35,0,1)
    return 1+trust*(1.35*fit-.55)


def _instrument_keys(times, pitches, weights, lanes, period, size):
    """Fit the contour into the available keys, including re-picks on the same key."""
    result = np.zeros(size, dtype=int)
    if not len(times):
        return result
    pitch = pitches[times]
    target = _pitch_register(times, pitch, weights[times], period, size)[times] * (lanes-1)
    keys = np.arange(lanes)
    motion = keys[None, :] - keys[:, None]
    back = np.zeros((len(times), lanes), dtype=np.uint8)
    cost = (keys - target[0])**2
    for i in range(1, len(times)):
        delta = pitch[i] - pitch[i-1]
        if times[i] - times[i-1] > period*3:
            change = .025 * abs(motion)
        elif delta == 0:
            change = 8.0 * abs(motion)
        else:
            direction = np.sign(delta)*motion
            change = 2.5*(direction == 0) + 5.*(direction < 0) + .15*(abs(motion)-min(3, 1+abs(delta)//5))**2
        paths = cost[:, None] + change
        back[i] = paths.argmin(axis=0)
        cost = paths.min(axis=0) + (keys - target[i])**2
    key = int(cost.argmin())
    for i in reversed(range(len(times))):
        result[times[i]] = key
        key = back[i, key]
    return result


def _instrument_tails(times, pitches, activity, level, rank):
    """Measure pitch release and stop at the next audible pick of the same pitch."""
    tails = {}
    for i, j in enumerate(times):
        p = pitches[j]
        end = min(len(level)-1, j+round(3.1*FPS))
        for k in times[i+1:np.searchsorted(times, end)]:
            if rank[k] >= max(.65, .42*rank[j]) and pitches[k] == p:
                end = k - round(.07*FPS)
                break
        if end-j < .36*FPS:
            continue
        env = activity[j:end+1, p]
        body = env[:8].max()
        if body < 100:
            continue
        amplitude = np.max(level[j:min(end+1,j+10)])
        active = (env >= max(42, .42*body)) & (level[j:end+1] > max(.0003, .24*amplitude))
        started = np.flatnonzero(active[:7])
        if not len(started):
            continue
        after = int(started[0])
        gap = np.flatnonzero(np.convolve(~active[after:], np.ones(4, int), mode="valid") == 4)
        tail = j+after+int(gap[0]) if len(gap) else end
        if tail-j >= .36*FPS:
            tails[j] = tail
    return tails


def _playable_holds(chart, tails, difficulty, head_frames=None):
    # Reserve the string for the duration of its sound. An independent drum
    # can use the neighbouring key, including during a sustained bass line.
    lanes = {"easy": 3, "medium": 4, "hard": 5}[difficulty]
    occupied = [-1.] * lanes
    for row in chart:
        lane, t = row["lane"], row["t"]
        if row["part"] in ("kick", "snare", "hat") and occupied[lane] > t:
            available = [k for k in range(lanes) if occupied[k] <= t]
            if available:
                row["lane"] = min(available, key=lambda k: abs(k-lane))
        elif row["part"] in tails and not row["sync"] and not row.get("chord"):
            frame = head_frames[t] if head_frames is not None else round(t*FPS)
            until = tails[row["part"]].get(frame)
            if until is not None:
                row["duration"] = round(until/FPS-t, 4)
                occupied[lane] = until/FPS+.07
    next_on_lane = [float("inf")]*lanes
    next_head = float("inf")
    for row in reversed(chart):
        lane, t = row["lane"], row["t"]
        if "duration" in row:
            end = min(t+row["duration"], next_on_lane[lane]-.075)
            if difficulty == "easy":
                end = min(end, next_head-.075)
            if end-t >= (.46 if difficulty == "easy" else .36):
                row["duration"] = round(end-t, 4)
            else:
                del row["duration"]
        next_on_lane[lane] = next_head = t


def _instrument_map(data):
    rms = data["rms"]
    size = len(rms)
    frames = np.arange(size)
    mix_ref = _phrase_scale(rms, 85, max(.001, np.percentile(rms, 90)*.07))
    presence = {part: _source_presence(data[part + "_level"], rms, mix_ref) for part in ("lead", "bass", "drums")}
    lead_level=data["lead_level"]
    if "comp_level" in data:
        lead_level=.45*lead_level+.55*data["comp_level"]
        presence["lead"]=_source_presence(lead_level,rms,mix_ref)
    lines = {p: _instrument_line(data, p, presence[p]) for p in ("lead", "bass")}
    # Percussive events must originate in the separated kit. Use stable keys
    # for kick, snare and hat when a pitched instrument leaves space.
    low, low_mid, mid, high, air = data["drums_flux"].T
    bands = np.stack((low+.15*low_mid, .47*low_mid+.36*mid+.17*high, .28*high+.72*air), axis=1)
    drum_curves = np.column_stack([v/_phrase_scale(v, 90, max(.055, .24*np.percentile(v, 90)))
                                   for v in bands.T])
    drum_curves *= np.array([1., .99, .74])
    kit = drum_curves.max(axis=1)*presence["drums"]
    kit_times = _peaks(kit, .42)
    kit_times = np.asarray(_select(kit_times, kit, .078*FPS), dtype=int)
    drums = np.zeros(size)
    drums[kit_times] = np.minimum(kit[kit_times], 3)
    lead, bass = (lines[p][0] for p in ("lead", "bass"))
    bpm, beats, confidence = _beat_track(np.minimum(kit, 3), np.maximum(lead, bass))
    period = 60*FPS/bpm
    # A phrase stays with an audible instrumental line. When the line rests
    # or drops back in the mix, let the bass and kit carry the next phrase.
    window = min(size, max(5, round(3*period)))
    phrase_level = np.convolve(np.minimum(1, lead_level/mix_ref), np.ones(window)/window, mode="same")
    density = np.convolve(np.minimum(1, lead), np.ones(window)*FPS/window, mode="same")
    foreground = np.clip((phrase_level-.075)/.24, 0, 1) * np.clip((density-.7)/1.8, 0, 1)
    phase = np.interp(frames, beats, np.arange(len(beats)))
    reliable = (np.interp(frames, beats, confidence) >= .16) & (frames >= beats[0]) & (frames <= beats[-1])
    lead_groove = np.where(reliable,_groove_weights(lead,phase,period),1.)
    bass_groove = np.where(reliable,_groove_weights(bass,phase,period),1.)
    bass_body = np.convolve(np.minimum(1,data["bass_level"]/mix_ref),np.ones(window)/window,mode="same")
    bass_front = np.clip((bass_body-.065)/.24,0,1)*np.clip(1.4-.8*foreground,0,1)
    ranks = np.column_stack((lead*(1.15+1.26*foreground)*lead_groove,
                             bass*(1.01-.23*foreground+.55*bass_front)*bass_groove,
                             drums*(1.14-.51*foreground)))
    for position, pitch, amplitude in data["repeats"]:
        j = int(position)
        if phrase_level[j] >= .19 or amplitude < .12*mix_ref[j]:
            continue
        part = "bass" if pitch < 32 and presence["bass"][j] >= .65 else "lead"
        column = 1 if part == "bass" else 0
        lo, hi = max(0,j-3), min(size,j+4)
        closest = lo+int(ranks[lo:hi,column].argmax())
        if ranks[closest,column] >= .65:
            j = closest
        ranks[j,column] = max(ranks[j,column], 1.15+min(1.,amplitude/max(.002,mix_ref[j])))
        lines[part][1][j] = int(pitch)
    ring = _ringing_bass(data, kit_times[drums[kit_times]>=1.0])
    for hit, (pitch, end) in ring.items():
        for column, part in enumerate(("lead","bass")):
            line = lines[part]
            adjacent = np.arange(hit+1,end)
            same = (line[1][adjacent]>=0) & ((line[1][adjacent]-pitch)%12==0)
            ranks[adjacent[same],column] *= .20
        lines["bass"][1][hit] = pitch
        ranks[hit,1] = max(3.8,ranks[hit].max()+.45)
    parts = ranks.argmax(axis=1)
    best = ranks.max(axis=1)
    events = np.flatnonzero(best >= .45)
    if not len(events):
        return _recording_map(data, bpm, {mode: [] for mode in ("easy","medium","hard")}, [])
    beat = np.where(reliable, np.exp(-.5*((phase - np.rint(phase))/.10)**2), .5)
    halves = np.where(reliable, np.exp(-.5*((2*phase - np.rint(2*phase))/.17)**2), .5)
    tails = {p: _instrument_tails(np.flatnonzero(line[0]), line[1], data[p+"_activity"],
                                  data[p+"_level"], line[0]) for p, line in lines.items()}
    tails["bass"].update({hit:end for hit, (_,end) in ring.items()})
    charts, shared = {}, set()
    names = ("lead", "bass", "kick", "snare", "hat")
    drum_types = drum_curves.argmax(axis=1)
    offsets=(_attack_offsets(lines["lead"][3]),_attack_offsets(lines["bass"][3]),_attack_offsets(kit))
    settings = (("easy", 3, .86, np.clip(.61*period, .26*FPS, .42*FPS), .46),
                ("medium", 4, .70, np.clip(.34*period, .145*FPS, .235*FPS), .24),
                ("hard", 5, .62, np.clip(.15*period, .071*FPS, .112*FPS), .07))
    pools = {}
    # Reduce one fully articulated performance for the easier levels. An early
    # simplification must not displace both notes of a fast pair on five keys.
    allowed = events
    repicks=None
    if "comp_art" in data:
        pick = data["comp_art"] @ [.01,.03,.13,.23,.26,.23,.10,.01]
        envelope = pick/_phrase_scale(pick,88,max(.04,.15*np.percentile(pick,90)))*presence["lead"]
        repicks = envelope,parts==0
    for mode, lanes, threshold, distance, weight in reversed(settings):
        ranking = best*(1-weight + weight*(.74*beat+.26*halves))
        positions = _phrase_heads(allowed[ranking[allowed]>=threshold], ranking, distance,
                                  repicks if mode=="hard" else None)
        if not positions:
            positions = [int(allowed[np.argmax(ranking[allowed])])]
        pools[mode] = positions,ranking
        allowed = np.asarray(positions,dtype=int)
    head_times=_head_times(data,pools["hard"][0],parts,offsets)
    head_frames={timestamp:j for j,timestamp in head_times.items()}
    for mode, lanes, threshold, distance, weight in settings:
        positions,ranking = pools[mode]
        if mode == "easy":
            for left in range(0, size, max(1,round(16*period))):
                group = positions[bisect_left(positions,left):bisect_left(positions,left+round(16*period))]
                if group:
                    shared.add(max(group, key=lambda j: (j not in tails["bass"] and j not in tails["lead"], ranking[j])))
        keys = []
        for index, (part, line) in enumerate(lines.items()):
            times = np.asarray([j for j in positions if parts[j] == index], dtype=int)
            keys.append(_instrument_keys(times, line[1], ranks[:,index], lanes, period, size))
        chart = []
        for j in positions:
            part = int(parts[j])
            if part == 2:
                part += int(drum_types[j])
                lane = (0, lanes//2, lanes-1)[part-2]
            else:
                lane = int(keys[part][j])
            t = head_times[j]
            row = {"t": t, "lane": lane, "part": names[part], "sync": j in shared}
            chart.append(row)
            if mode == "hard" and part == 0 and not row["sync"]:
                p, q = lines["lead"][1][j], lines["lead"][2][j]
                if q >= 0 and foreground[j] >= .45:
                    step = 1 if abs(q-p) <= 5 else 2
                    row["lane"] = min(lane, lanes-1-step)
                    chart.append(dict(row, lane=row["lane"]+step, chord=True))
        _playable_holds(chart, tails, mode, head_frames)
        charts[mode] = chart

    audible = rms >= max(.00015, np.percentile(rms, 93)*.019)
    beats = [round(int(j)/FPS, 4) for j in beats if reliable[j] and audible[j]]
    return _recording_map(data, bpm, charts, beats)


def _recording_map(data, bpm, charts, beats):
    duration = float(data["duration"])
    rms = data["rms"]
    waveform = [round(float(block.max()), 3) for block in np.array_split(rms, min(96, len(rms)))]
    level = max(max(waveform), 0.001)
    return {"duration": round(duration, 4), "bpm": bpm, "engine": ENGINE,
            "analysisVersion": VERSION, "charts": charts, "beats": beats,
            "waveform": [round(value / level, 3) for value in waveform]}


def analyze(path: Path, progress=None, *, performance_directory=None) -> dict:
    notify = progress or (lambda _: None)
    from performances import identify, reviewed_chart
    from reductions import refine
    from song_phrases import arrange
    mix = _decode(path)
    duration = len(mix) / SAMPLE_RATE
    match = identify(path, duration)
    if match:
        notify("Preparando o arranjo desta gravação")
        return refine(reviewed_chart(match, duration))
    featured = {} if performance_directory is not None else None
    data = _instrument_features(path, notify, mix, featured=featured)
    notify("Escolhendo as frases de voz, riff, baixo e bateria")
    chart = refine(arrange(_instrument_map(data), data))
    if featured and chart.get("performance"):
        from performances import render
        section_info=chart["performance"]
        notify("Salvando a parte que você toca")
        target=performance_directory / f"instrumento-r{section_info['revision']}.flac"
        try:
            render(path,target,section_info["sections"],chart["charts"]["hard"],stems=featured)
        except (OSError,RuntimeError,subprocess.SubprocessError):
            import logging
            logging.warning("O áudio jogável será preparado na partida.",exc_info=True)
    return chart
