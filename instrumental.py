"""Separate the playable instruments on the recording's sample clock."""
import os
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np
import onnxruntime as ort


@lru_cache(maxsize=4)
def _separator(part):
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    path = Path(__file__).parent / "models" / "umxhq" / (part + ".int8.onnx")
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def separate_instruments(path, sample_count, progress, *, stereo=False, include_voice=False, vocal_cues=None):
    """Return aligned source images and optionally measure syllables in the same model pass."""
    window_size, hop, halo, chunk = 4096, 512, 48, 512
    window = np.hanning(window_size + 1)[:-1].astype(np.float32)
    window_out = window[::2]
    output_hop = hop // 2
    count = (sample_count + output_hop - 1) // output_hop
    padded_size = count * output_hop + len(window_out)
    channels = 2 if stereo else 1
    parts_count = 4 if include_voice else 3
    audio = np.zeros((parts_count*channels, padded_size), dtype=np.float32)
    if vocal_cues is not None:
        from vocal_pitch import estimate_pitch, vocal_pitch_confidence
        edges = np.searchsorted(np.arange(1116)*44100/window_size,
                                [100,220,400,650,1000,1600,2500,3800,6000,10000])
        voice = np.empty((count,len(edges)-1),np.float32)
        mix_voice = np.empty_like(voice)
        pitch, confidence = np.empty(count,np.float32), np.empty(count,np.float32)
    weight = np.zeros(padded_size, dtype=np.float32)
    # Interpolate masks with context, but reconstruct with the original phase
    # at every hop. Skipping analysis frames must never skip audio samples.
    buffer = np.zeros((halo * hop + window_size // 2, 2), np.float32)
    cmd = [os.environ.get("FFMPEG_PATH") or "ffmpeg", "-nostdin", "-v", "error",
           "-protocol_whitelist", "file,pipe", "-i", str(Path(path).resolve()),
           "-map", "0:a:0", "-vn", "-sn", "-dn", "-ac", "2", "-ar", "44100",
           "-f", "f32le", "pipe:1"]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    names = ("other", "bass", "drums", "vocals")
    last_percent = -1
    try:
        for start in range(0, count, chunk):
            width = min(chunk, count - start)
            length = window_size + (width + 2 * halo) * hop
            raw = process.stdout.read((length - len(buffer)) * 8)
            buffer = np.concatenate((buffer, np.frombuffer(raw, "<f4").reshape(-1, 2)))
            if len(buffer) < length:
                buffer = np.pad(buffer, ((0, length - len(buffer)), (0, 0)))
            frames = np.lib.stride_tricks.sliding_window_view(buffer, window_size, axis=0)[::hop]
            spectrum = np.fft.rfft(frames * window).astype(np.complex64)
            net_input = np.ascontiguousarray(np.abs(spectrum[::2, :, :1487]))
            targets = np.arange(halo, halo + width)
            masks = []
            for part in names:
                estimate = _separator(part).run(["mask"], {"mix": net_input})[0]
                if part == "vocals" and vocal_cues is not None:
                    around = np.arange(halo-1,halo+width+1)
                    magnitude = np.abs(spectrum[around,:,:1116])
                    voice_mask = .5*(estimate[around//2]+estimate[(around+1)//2])
                    isolated = (magnitude*np.minimum(voice_mask,1)).mean(axis=1)
                    region = slice(start,start+width)
                    for dest, spec in ((voice,isolated[1:-1]),(mix_voice,magnitude[1:-1].mean(axis=1))):
                        for band,(lo,hi) in enumerate(zip(edges,edges[1:])):
                            dest[region,band] = np.linalg.norm(spec[:,lo:hi],axis=1)
                    if start == 0: isolated[0] = isolated[1]
                    if start+width == count: isolated[-1] = isolated[-2]
                    pitch[region], confidence[region] = estimate_pitch(isolated)
                mask = (estimate[targets // 2, :, :1025] + estimate[(targets+1) // 2, :, :1025]) * .5
                masks.append(np.square(mask))
            total = np.maximum(np.sum(masks, axis=0), .04)
            source = spectrum[halo:halo + width, :, :1025]
            images = np.stack([source * (mask / total) for mask in masks[:parts_count]])
            parts = (images.transpose(0,2,1,3).reshape(parts_count*2,width,1025) if stereo
                     else images.mean(axis=2))
            parts[:, :, -1] = 0  # Nyquist of the downsampled signal.
            waves = (np.fft.irfft(parts, n=len(window_out)) * (.5 * window_out)).astype(np.float32)
            for phase in range(len(window_out) // output_hop):
                batch = waves[:, phase::8]
                left = (start + phase) * output_hop
                right = left + batch.shape[1] * len(window_out)
                if right > left:
                    audio[:, left:right] += batch.reshape(parts_count*channels, -1)
                    weight[left:right].reshape(-1, len(window_out))[:] += window_out**2
            buffer = buffer[width * hop:]
            percent = (start + width) * 10 // count
            if percent != last_percent:
                progress(f"Isolando os instrumentos · {percent * 10}%")
                last_percent = percent
        if process.wait(timeout=30):
            raise ValueError("O áudio não pôde ser lido para separar os instrumentos.")
    finally:
        if process.poll() is None:
            process.kill()
        process.stdout.close()
        process.wait()
    keep = slice(len(window_out)//2, len(window_out)//2 + sample_count)
    result = audio[:, keep]
    result /= np.maximum(weight[keep], 1.e-7)
    if vocal_cues is not None:
        vocal_cues.update(voice_bands=voice,mix_voice_bands=mix_voice,voice_midi=pitch,
                          voice_pitch_confidence=vocal_pitch_confidence(confidence,voice,mix_voice))
    return result.reshape(parts_count,2,sample_count) if stereo else result


@lru_cache(maxsize=1)
def _accompaniment_model():
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    providers=[p for p in ("DmlExecutionProvider","CPUExecutionProvider") if p in ort.get_available_providers()]
    if "DmlExecutionProvider" in providers:
        options.enable_mem_pattern=False
    model = Path(__file__).parent / "models" / "mdx" / "other.onnx"
    try:
        return ort.InferenceSession(str(model),sess_options=options,providers=providers)
    except (RuntimeError, ort.capi.onnxruntime_pybind11_state.Fail,
            ort.capi.onnxruntime_pybind11_state.RuntimeException):
        if "DmlExecutionProvider" not in providers:
            raise
        return ort.InferenceSession(str(model),sess_options=options,providers=["CPUExecutionProvider"])


def accompaniment(path, sample_count, progress, *, stereo=False):
    """A second estimate of guitar, keys and strings, on the same clock as the first."""
    model = _accompaniment_model()
    size, hop, margin, keep = 8192, 1024, 32, 448
    output_hop = hop//2
    count = (sample_count+output_hop-1)//output_hop
    window = np.hanning(size+1)[:-1].astype(np.float32)
    synthesis_window = window[::2]
    channels = 2 if stereo else 1
    out = np.zeros((channels,count*output_hop+size//2),np.float32)
    norm = np.zeros(out.shape[1],np.float32)
    buffer = np.zeros((size//2+margin*hop,2),np.float32)
    cmd=[os.environ.get("FFMPEG_PATH") or "ffmpeg","-nostdin","-v","error","-protocol_whitelist","file,pipe",
         "-i",str(Path(path).resolve()),"-map","0:a:0","-vn","-sn","-dn","-ac","2","-ar","44100","-f","f32le","pipe:1"]
    proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                          creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    try:
        for left in range(0,count,keep):
            n=min(keep,count-left)
            required=size+(n+2*margin-1)*hop
            pcm=proc.stdout.read((required-len(buffer))*8)
            buffer=np.concatenate((buffer,np.frombuffer(pcm,"<f4").reshape(-1,2)))
            if len(buffer)<required:
                buffer=np.pad(buffer,((0,required-len(buffer)),(0,0)))
            frames=np.lib.stride_tricks.sliding_window_view(buffer,size,axis=0)[::hop]
            indices=np.clip(left-margin+np.arange(512),0,count-1)-left+margin
            spectrum=np.fft.rfft(frames[indices]*window).astype(np.complex64)[:,:,:2048]
            components=spectrum.transpose(1,2,0)
            packed=np.stack((components.real,components.imag),axis=1).reshape(1,4,2048,512)
            packed[:,:,:3]=0
            prediction=model.run(None,{model.get_inputs()[0].name:np.ascontiguousarray(packed)})[0]
            components=prediction.reshape(2,2,2048,512)
            if not stereo:
                components=components.mean(axis=0,keepdims=True)
            spec=(components[:,0]+1j*components[:,1])[:,:,margin:margin+n].transpose(0,2,1)
            waves=(np.fft.irfft(spec,n=size//2)*(.5*synthesis_window)).astype(np.float32)
            for phase in range(len(synthesis_window)//output_hop):
                block=waves[:,phase::8]
                start=(left+phase)*output_hop
                stop=start+block.shape[1]*len(synthesis_window)
                if stop>start:
                    out[:,start:stop]+=block.reshape(channels,-1)
                    norm[start:stop].reshape(-1,len(synthesis_window))[:]+=synthesis_window**2
            buffer=buffer[n*hop:]
            progress(f"Conferindo os ataques do acompanhamento · {(left+n)*100//count}%")
        if proc.wait(timeout=30):
            raise ValueError("O áudio não pôde ser lido para analisar o acompanhamento.")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.wait()
    valid=slice(size//4,size//4+sample_count)
    separated=out[:,valid]/np.maximum(norm[valid],1.e-7)
    return separated if stereo else separated[0]


def attack_features(signal, sample_rate, hop):
    """Spectral changes of one isolated instrument, measured on short windows."""
    size = 1024
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(signal, size//2), size)[::hop]
    count = (len(signal) + hop - 1) // hop
    window = np.hanning(size).astype(np.float32)
    freq = np.fft.rfftfreq(size, 1 / sample_rate)
    edges = np.searchsorted(freq, [30, 165, 550, 2200, 5200, 11000])
    flux = np.zeros((count, 5), np.float32)
    level = np.zeros(count, np.float32)
    previous = np.zeros((2, len(freq)), np.float32)
    for start in range(0, count, 512):
        stop = min(start + 512, count)
        block = frames[start:stop]
        level[start:stop] = np.sqrt(np.mean(block * block, axis=1))
        spec = np.log1p(2.0 * np.abs(np.fft.rfft(block * window))).astype(np.float32)
        joined = np.concatenate((previous, spec))
        past = np.pad(joined[:-2], ((0, 0), (1, 1)), mode="edge")
        neighbourhood = np.maximum.reduce([past[:, i:i+len(freq)] for i in range(3)])
        growth = np.maximum(0, spec - neighbourhood)
        for i, (lo, hi) in enumerate(zip(edges, edges[1:])):
            flux[start:stop, i] = growth[:, lo:hi].mean(axis=1)
        previous = joined[-2:]
    # Two-hop difference is centered one hop before the later frame.
    return flux[np.minimum(np.arange(count)+1, count-1)], level


def articulation_features(signal, sample_rate, hop):
    """Octave-band attack envelopes and harmonic fingerprints of a separated line."""
    size = 2048
    count = (len(signal)+hop-1)//hop
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(signal,size//2),size)[::hop]
    window = np.hanning(size).astype(np.float32)
    # Keep the fretted register even when distortion makes the higher partials louder.
    midi = np.arange(28, 85)
    positions = size*440*2**((midi-69)/12)/sample_rate
    bins = np.floor(positions).astype(int)
    frac = positions-bins
    edges = np.searchsorted(np.fft.rfftfreq(size,1/sample_rate), [35,90,180,360,720,1440,2880,5760,10000])
    powers = np.empty((count,len(edges)-1),np.float32)
    tones = np.empty((count,len(midi)),np.float32)
    for left in range(0,count,384):
        right=min(count,left+384)
        mag=np.abs(np.fft.rfft(frames[left:right]*window))
        tones[left:right]=(mag[:,bins]*(1-frac)+mag[:,bins+1]*frac)*(2/window.sum())
        square=mag*mag
        powers[left:right]=np.column_stack([square[:,a:b].sum(axis=1) for a,b in zip(edges,edges[1:])])
    # Measure the rise across a pick and the change in its pitched body separately.
    # A long stationary chord has energy but neither cue repeats during the hold.
    scale=np.maximum(.015,.014*np.percentile(powers,90,axis=0))
    log_power=np.log1p(powers/scale)
    edges=np.pad(log_power,((4,4),(0,0)),mode="edge")
    short=np.maximum(0,edges[5:5+count]-edges[3:3+count])
    wide=np.maximum(0,edges[6:6+count]-edges[1:1+count])
    return (.58*short+.42*wide).astype(np.float32),tones


def bass_resonance(signal, sample_rate, hop):
    """Amplitude at bass fundamentals, also when a synth bass spills between stems."""
    size = 4096
    keys = np.arange(28, 53)
    bins = size * 440 * 2**((keys-69)/12) / sample_rate
    low = bins.astype(int)
    frac = bins-low
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(signal, size//2), size)[::hop]
    count = (len(signal)+hop-1)//hop
    result = np.empty((count,len(keys)), dtype=np.float32)
    window = np.hanning(size).astype(np.float32)
    for start in range(0,count,384):
        end = min(start+384,count)
        magnitude = np.abs(np.fft.rfft(frames[start:end]*window))
        result[start:end] = ((1-frac)*magnitude[:,low] + frac*magnitude[:,low+1]) * (2/window.sum())
    return result


def repeating_tones(signal, sample_rate, hop):
    """Recover tuned, regularly re-triggered samples that a separator calls voice."""
    size = 2048
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(signal,size//2),size)[::hop]
    count = (len(signal)+hop-1)//hop
    midi = np.arange(43,86)
    frequency = 440 * 2**((midi - 69)/12)
    bins = np.rint(size*frequency/sample_rate).astype(int)
    window = np.hanning(size).astype(np.float32)
    energy = np.empty((count,len(midi)),np.float32)
    tuning = np.empty_like(energy)
    phase = np.exp(-2j*np.pi*frequency*hop/sample_rate)
    previous = np.zeros(len(midi),np.complex64)
    for left in range(0,count,384):
        right = min(count,left+384)
        spec = np.fft.rfft(frames[left:right]*window)
        current = spec[:,bins]
        # A harmonic only gets a vote if it is a spectral maximum of its own.
        peak = (np.abs(current)>=np.abs(spec[:,bins-1])) & (np.abs(current)>=np.abs(spec[:,bins+1]))
        energy[left:right] = 2*np.abs(current)/window.sum() * peak
        past = np.concatenate((previous[None,:],current[:-1]))
        hz = frequency + np.angle(current*past.conj()*phase) * sample_rate/(2*np.pi*hop)
        tuning[left:right] = 1200*np.log2(np.maximum(1,hz)/frequency)
        previous = current[-1]
    # Candidate starts are measured in the signal, not filled at predicted beats.
    e = np.pad(energy,((5,6),(0,0)),mode="edge")
    new = (e[7:count+7]+e[8:count+8]+e[9:count+9])/3
    old = (e[:count]+e[1:count+1]+e[2:count+2])/3
    rise = new-old
    gate = np.maximum(.007, .08*np.percentile(energy,98,axis=0))
    indices, pitches = np.nonzero((rise>=np.roll(rise,1,axis=0)) & (rise>np.roll(rise,-1,axis=0)) &
                                  (rise>gate) & (new>1.6*old+gate) & (new>.012))
    per_pitch = [[] for _ in midi]
    for j,p in zip(indices,pitches):
        if j+14 >= count:
            continue
        tail = slice(j+4,j+13)
        cents = tuning[tail,p]
        if (np.ptp(cents) > 12 or abs(np.median(cents)) > 42 or
                np.min(energy[tail,p]) < .24*new[j,p]):
            continue
        history = per_pitch[p]
        if history and j-history[-1][0] < .12*sample_rate/hop:
            continue
        history.append((int(j),float(np.median(cents)),float(new[j,p])))
    result = {}
    for p,history in enumerate(per_pitch):
        if len(history)<3:
            continue
        stamps, cents, loudness = np.array(history).T
        for i in range(1,len(history)-1):
            a,b,c = stamps[i-1:i+2]
            if b-a > 4.2*sample_rate/hop or abs((b-a)-(c-b)) > max(3,.04*(b-a)):
                continue
            if np.ptp(cents[i-1:i+2])>9 or min(loudness[i-1:i+2]) < .29*max(loudness[i-1:i+2]):
                continue
            for j,_,level in history[i-1:i+2]:
                # The fundamental and its overtones start together; keep the
                # lowest comparable member when describing the player's note.
                previous = result.get(j)
                if previous is None or level>1.6*previous[1]:
                    result[j] = (int(midi[p])-21,level)
    return np.asarray([(j,p,v) for j,(p,v) in sorted(result.items())],dtype=np.float32).reshape(-1,3)
