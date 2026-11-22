"""Recognize a reviewed recording and prepare its playable instrument from local audio."""
from copy import deepcopy
from functools import lru_cache
import json
import os
from pathlib import Path
import subprocess
import tempfile

import numpy as np

DIRECTORY = Path(__file__).with_name("arrangements")
CATALOG_REVISION = 1
SIGNATURE_RATE = 8000
SIGNATURE_HOP = 80


@lru_cache(maxsize=1)
def catalog():
    return tuple(json.loads(file.read_text(encoding="utf8")) for file in sorted(DIRECTORY.glob("*.json")))


def candidates(duration):
    return [item for item in catalog() if abs(item["duration"] - duration) < 1.35]


def signature(pcm):
    size = 512
    frames = np.lib.stride_tricks.sliding_window_view(pcm, size)[::SIGNATURE_HOP]
    power = np.abs(np.fft.rfft(frames * np.hanning(size)))**2
    edges = np.array([3, 6, 10, 15, 22, 32, 47, 69, 100, 145, 213])
    bands = np.column_stack([power[:,a:b].sum(axis=1) for a,b in zip(edges,edges[1:])])
    bands = np.log(np.maximum(1.e-9, bands))
    return bands - np.median(bands, axis=0)


def identify(path, duration):
    items = candidates(duration)
    if not items:
        return None
    from analysis_engine import _decode
    pcm = _decode(path, SIGNATURE_RATE)
    for item in items:
        shifts = []
        for mark in item["signature"]:
            ref = np.asarray(mark["values"], dtype=float)/12
            begin = round((mark["time"]-1.4)*SIGNATURE_RATE)
            window = pcm[begin:begin+(len(ref)+280)*SIGNATURE_HOP+512]
            if len(window)<len(ref)*SIGNATURE_HOP+512:
                break
            features = signature(window)
            view = np.lib.stride_tricks.sliding_window_view(features, len(ref), axis=0).transpose(0,2,1)
            centered = view - np.median(view, axis=1, keepdims=True)
            dot = np.einsum("ijk,jk->i",centered,ref)
            scores = dot / np.maximum(1.e-7,np.linalg.norm(centered,axis=(1,2))*np.linalg.norm(ref))
            k = int(np.argmax(scores))
            if scores[k]<.85:
                break
            shifts.append((begin+k*SIGNATURE_HOP)/SIGNATURE_RATE-mark["time"])
        if len(shifts)==len(item["signature"]) and np.ptp(shifts)<=.025:
            return item, round(float(np.median(shifts)), 3)
    return None


def reviewed_chart(match, duration):
    from analysis_engine import VERSION
    item, shift = match
    chart = deepcopy(item["map"])
    chart["analysisVersion"] = VERSION
    chart["duration"] = round(duration, 4)
    for notes in chart["charts"].values():
        for n in notes:
            n["t"] = round(max(0,n["t"]+shift),4)
    chart["beats"] = [round(b+shift,4) for b in chart["beats"] if 0<=b+shift<duration]
    sections = deepcopy(item["sections"])
    for s in sections:
        s["start"] = round(max(0,s["start"]+shift),4)
        s["end"] = round(min(duration,s["end"]+shift),4)
    chart["performance"] = {"id":item["id"],"revision":CATALOG_REVISION,"sections":sections}
    return chart


def _playable_windows(notes, sections):
    """Include the bass answer or drum accent even underneath a different lead."""
    weights=[]
    section=0
    for note in notes:
        if note.get("chord"):continue
        t=note["t"]
        while section+1<len(sections) and sections[section]["end"]<=t:section+=1
        part=note["part"]
        role="drums" if part in ("kick","snare","hat") else part
        body=.19 if role=="drums" else .28
        length=max(note.get("duration",0),body)
        weights.append((t-.040,t+length+.065,role,.99 if role==sections[section]["role"] else .90))
        if note.get("accent") in ("kick","snare"):
            weights.append((t-.040,t+.24,"drums",.90))
    return weights


def render(path, destination, sections, notes=None, stems=None):
    """Encode a quad FLAC. The first pair is the mix, the second the current play-along part."""
    from instrumental import accompaniment, separate_instruments
    ffmpeg = os.environ.get("FFMPEG_PATH") or "ffmpeg"
    hidden = getattr(subprocess,"CREATE_NO_WINDOW",0)
    raw = subprocess.run([ffmpeg,"-nostdin","-v","error","-protocol_whitelist","file,pipe","-i",str(path),
                          "-map","0:a:0","-vn","-ac","2","-ar","44100","-f","f32le","pipe:1"],
                         check=True,capture_output=True,creationflags=hidden).stdout
    mix = np.frombuffer(raw,"<f4").reshape(-1,2)
    count = (len(mix)+1)//2
    notify = lambda _: None
    windows = (_playable_windows(notes,sections) if notes is not None else
               [(s["start"]-.045,s["end"]+.045,s["role"],1.) for s in sections])
    available = {w[2] for w in windows}
    stems = dict(stems or {})
    if (available-stems.keys()) & {"bass","drums","voice"}:
        sources = separate_instruments(path,count,notify,stereo=True,include_voice="voice" in available)
        stems.update(bass=sources[1],drums=sources[2])
        if "voice" in available: stems["voice"] = sources[3]
    if "lead" in available and "lead" not in stems:
        stems["lead"] = accompaniment(path,count,notify,stereo=True)
    directory = destination.parent
    directory.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory,prefix=".performance-") as temp:
        target = Path(temp)/"band.flac"
        cmd=[ffmpeg,"-nostdin","-v","error","-f","f32le","-ar","44100","-ac","4","-channel_layout","quad",
             "-i","pipe:0","-c:a","flac","-sample_fmt","s32","-compression_level","3",str(target)]
        with subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=hidden) as proc:
            cursor=0
            for left in range(0,len(mix),65536):
                right = min(len(mix),left+65536)
                idx = np.arange(left,right)
                t = idx/44100
                focus = np.zeros((len(idx),2),np.float32)
                while cursor<len(windows) and windows[cursor][1]<t[0]:cursor+=1
                envelopes={}
                i=cursor
                while i<len(windows) and windows[i][0]<=t[-1]:
                    start,end,role,amount=windows[i]
                    i+=1
                    if end<t[0]:continue
                    if role not in envelopes: envelopes[role]=np.zeros(len(t),np.float32)
                    envelope=envelopes[role]
                    first,last=np.searchsorted(t,[start,end],side="left")
                    local=t[first:last]
                    fade=.09 if notes is None else .04
                    gain=amount*np.clip(np.minimum((local-start)/fade,(end-local)/(fade if notes is None else .065)),0,1)
                    np.maximum(envelope[first:last],gain,out=envelope[first:last])
                for role,weight in envelopes.items():
                    stem = stems[role]
                    j=np.minimum(idx//2,stem.shape[1]-1)
                    # Upsample the isolated image while preserving its original stereo position.
                    a=stem[:,j].T
                    b=stem[:,np.minimum(j+1,stem.shape[1]-1)].T
                    focus+=(a+(b-a)*((idx%2)*.5)[:,None])*weight[:,None]
                proc.stdin.write(np.column_stack((mix[left:right],focus)).astype("<f4").tobytes())
            proc.stdin.close()
            if proc.wait():
                raise RuntimeError("Não foi possível preparar o instrumento jogável.")
        os.replace(target,destination)
