"""Recognize lead timbres on the mix. Note positions come from the separated instrument."""
from functools import lru_cache
from pathlib import Path

import numpy as np
import onnxruntime as ort


FAMILIES = ("Violão", "Guitarra", "Piano")
HOP_SECONDS = .48
MODEL = Path(__file__).parent / "models" / "yamnet" / "yamnet.fp16.onnx"


@lru_cache(maxsize=1)
def _network():
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    return ort.InferenceSession(str(MODEL), options, providers=["CPUExecutionProvider"])


@lru_cache(maxsize=1)
def _filters():
    # The upstream model uses triangular filters in HTK mel space over FFT magnitudes.
    frequencies = np.linspace(0, 8000, 257)
    mel = 1127 * np.log1p(frequencies / 700)
    edges = np.linspace(1127 * np.log1p(125 / 700), 1127 * np.log1p(7500 / 700), 66)
    rising = (mel[:, None] - edges[:-2]) / (edges[1:-1] - edges[:-2])
    falling = (edges[2:] - mel[:, None]) / (edges[2:] - edges[1:-1])
    return np.maximum(0, np.minimum(rising, falling)).astype(np.float32)


@lru_cache(maxsize=1)
def _resampling_kernel():
    taps = np.arange(-15,17)
    phase = np.arange(320)[:, None]/320
    kernel = np.sinc((taps-phase)*16000/22050) * np.kaiser(len(taps),7)
    return (kernel/kernel.sum(axis=1,keepdims=True)).astype(np.float32)


def from_analysis_rate(signal):
    """Convert the 22.05 kHz separated sound to the classifier's input rate."""
    count = (len(signal)*320+440)//441
    padded = np.pad(signal,(15,16))
    windows = np.lib.stride_tricks.sliding_window_view(padded,32)
    out = np.empty(count,np.float32)
    kernel = _resampling_kernel()
    for i in range(0,count,16384):
        positions = np.arange(i,min(i+16384,count))*441
        base, phase = np.divmod(positions,320)
        out[i:i+len(base)] = np.einsum("ij,ij->i", windows[base],kernel[phase])
    return timbres(out)


def timbres(pcm):
    """YAMNet on contiguous 16 kHz mono audio, every 480 ms, centered at 487.5 ms."""
    windows = max(1, 1 + (len(pcm) - 15600 + 7679) // 7680)
    padded = np.pad(pcm, (0, (windows-1)*7680 + 15600 - len(pcm)))
    frames = np.lib.stride_tricks.sliding_window_view(padded, 400)[::160]
    taper = np.hanning(401)[:-1].astype(np.float32)
    session = _network()
    result = []
    for start in range(0, windows, 24):
        count = min(24, windows-start)
        block = frames[start*48:(start+count-1)*48+96]
        magnitude = np.abs(np.fft.rfft(block*taper, 512)).astype(np.float32)
        logmel = np.log(magnitude @ _filters() + .001)
        patches = np.lib.stride_tricks.sliding_window_view(logmel, 96, axis=0)[::48].transpose(0,2,1)
        scores, = session.run(["scores"], {"patches":np.ascontiguousarray(patches)})
        # Bass guitar has its own AudioSet class. Including it as a guitar lead
        # would move bass-led verses onto the wrong playable instrument.
        result.append(np.column_stack((scores[:,138], scores[:,136], np.maximum(scores[:,148],scores[:,149]),
                                       scores[:,135], scores[:,141], scores[:,147])))
    return np.concatenate(result)


def family_profile(mixed,separated,features=None):
    """Compare sustained evidence on the mix and accompaniment in musical phrases."""
    def evidence(tags):
        acoustic,electric,piano,guitar,strum,keys=tags.T
        acoustic=np.sqrt(acoustic*np.maximum(strum,.41*guitar))/.16
        electric=np.sqrt(electric*np.maximum(electric,.68*guitar))/.115
        piano=np.sqrt(piano*np.maximum(.60*piano,keys))/.10
        return np.column_stack((acoustic,electric,piano))
    count=min(len(mixed),len(separated))
    mix=evidence(mixed[:count])
    both=np.maximum(mix,.81*evidence(separated[:count]))
    # A single bright chord can resemble a different instrument. Average over
    # its neighbours and use the performance's recurring timbre as a small prior.
    padded=np.pad(np.minimum(both,2.5),((2,2),(0,0)),mode="edge")
    smooth=sum(w*padded[i:i+count] for i,w in enumerate((.10,.22,.36,.22,.10)))
    prior=np.quantile(mix,.88,axis=0)
    smooth*=1+.23*np.minimum(1,prior)
    profile=np.clip((smooth-.15)/.77,0,1).astype(np.float32)
    if features is not None and np.mean(profile[:,2])>.28:
        # The short attack of a high piano key can sound like a plucked string.
        # Look for independent notes ringing more than two octaves apart across
        # the exposed performance before treating ambiguous attacks as keys.
        rms=features["rms"]
        audible=rms>max(.001,np.percentile(rms,35))
        if np.any(audible):
            notes=features["lead_activity"][audible]>95
            span=notes.shape[1]-1-np.argmax(notes[:,::-1],axis=1)-np.argmax(notes,axis=1)
            two_hands=np.mean(notes.any(axis=1)&(span>23))
            kit=np.mean(features["drums_level"][audible]/np.maximum(.005,rms[audible]))
            exposed=np.clip((.09-kit)/.07,0,1)*np.clip((two_hands-.07)/.11,0,1)
            # Keep strong contrary evidence. Ambiguous bars inherit the piano
            # only if the instrument also appears elsewhere in this recording.
            if exposed>0:
                prior=float(np.mean(profile[:,2]))
                profile[:,0]*=1-.54*exposed
                profile[:,2]=np.maximum(profile[:,2],exposed*prior*np.minimum(1,.3+profile[:,0]*2))
    return profile


def frame_index(t, count):
    return np.clip(np.rint((np.asarray(t)-.4875)/HOP_SECONDS).astype(int),0,count-1)
