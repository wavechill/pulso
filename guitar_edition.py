"""Load the author's five-fret chart and align its tick clock to a matching local recording."""
from bisect import bisect_right
from functools import lru_cache
from http.client import HTTPException
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.request import Request, urlopen

import numpy as np


CHART_URL = "https://files.enchor.us/7229be4a1730a8077687ff088e5547ef.sng"
DIGEST = "ac2e401fba329eea464d47630fdd2a9bcf8af1802aa1d6810fef374a983f7777"
# File index of Wagsii's published SNG. Fetch only the header and notes.chart.
CHART_START, CHART_BYTES, PACKAGE_BYTES = 406862, 80069, 2719505
_retry_after = 0.


def can_retry():
    return time.monotonic() >= _retry_after


def _part(first, size):
    last = first+size-1
    request = Request(CHART_URL, headers={"Range":f"bytes={first}-{last}", "User-Agent":"Pulso/1.12"})
    with urlopen(request,timeout=7) as response:
        if response.status == 206 and response.headers.get("Content-Range") == f"bytes {first}-{last}/{PACKAGE_BYTES}":
            return response.read(size)
        if response.status == 200 and response.headers.get("Content-Length") == str(PACKAGE_BYTES):
            # Some connections deliver the entire container despite Range.
            return response.read(first+size)[first:]
        raise ValueError("O servidor enviou um trecho diferente do mapa.")


def chart_text():
    global _retry_after
    data = Path(os.environ.get("PULSO_DATA_DIR") or Path(os.environ.get("LOCALAPPDATA") or Path.home())/"Pulso")
    file = data/"mapas"/"Wagsii - Chop Suey.chart"
    try:
        contents = file.read_bytes()
        if hashlib.sha256(contents).hexdigest() == DIGEST:
            return contents.decode("utf-8-sig")
    except OSError:
        pass
    if not can_retry():
        return None
    try:
        header = _part(0,26)
        if len(header)!=26 or header[:10] != b"SNGPKG\x01\0\0\0":
            raise ValueError("Formato de mapa desconhecido.")
        packed = _part(CHART_START,CHART_BYTES)
        contents = bytes(x ^ header[10+i%16] ^ (i%256) for i,x in enumerate(packed))
        if hashlib.sha256(contents).hexdigest() != DIGEST:
            raise ValueError("Não foi possível confirmar a edição de Wagsii.")
    except (OSError,HTTPException,ValueError):
        _retry_after = time.monotonic()+40
        return None
    # The chart remains playable for this session even on a read-only folder.
    try:
        file.parent.mkdir(parents=True,exist_ok=True)
        temp=None
        try:
            with tempfile.NamedTemporaryFile(dir=file.parent,delete=False) as handle:
                temp=Path(handle.name)
                handle.write(contents)
            os.replace(temp,file)
        finally:
            if temp is not None: temp.unlink(missing_ok=True)
    except OSError:
        pass
    return contents.decode("utf-8-sig")


def match_clock(marks, pcm):
    from performances import signature
    features = signature(pcm)
    found=[]
    for index,mark in enumerate(marks):
        t=mark["time"]
        ref=np.asarray(mark["values"],dtype=float)/12
        left=max(0,round((t-10)*100))
        window=features[left:round((t+10)*100)+len(ref)]
        if len(window)<len(ref): return None
        candidates=np.lib.stride_tricks.sliding_window_view(window,len(ref),axis=0).transpose(0,2,1)
        centered=candidates-np.median(candidates,axis=1,keepdims=True)
        similarity=np.einsum("ijk,jk->i",centered,ref)/np.maximum(1.e-8,np.linalg.norm(centered,axis=(1,2))*np.linalg.norm(ref))
        best=int(similarity.argmax())
        if similarity[best]>=.78:
            found.append((t,(left+best)/100))
        elif len(found)+len(marks)-index-1 < len(marks)-2:
            return None
    if len(found)<len(marks)-2: return None
    x,y=np.array(found).T
    # Repeated riffs can match the wrong bar. Fit the largest agreeing set,
    # requiring independent anchors near both ends of the performance.
    best=np.zeros(len(x),bool)
    for i in range(len(x)):
        for j in range(i+1,len(x)):
            if x[j]-x[i]<30: continue
            rate=(y[j]-y[i])/(x[j]-x[i])
            if not .985<=rate<=1.015: continue
            agrees=np.abs(y-(y[i]+rate*(x-x[i])))<.035
            if agrees.sum()>best.sum(): best=agrees
    if best.sum()<len(marks)-2 or x[best][0]>20 or x[best][-1]<180: return None
    scale,offset=np.polyfit(x[best],y[best],1)
    return float(scale),float(offset)


@lru_cache(maxsize=1)
def parse(text):
    sections={}
    for name,body in re.findall(r"\[([^\]]+)\]\s*\{([^}]*)\}",text):
        sections[name]=body
    resolution=int(re.search(r"\bResolution\s*=\s*(\d+)",sections["Song"])[1])
    base=float(re.search(r"\bOffset\s*=\s*([-\d.]+)",sections["Song"])[1])
    changes={0:120000}
    changes.update((int(t),int(b)) for t,b in re.findall(r"(\d+)\s*=\s*B\s+(\d+)",sections["SyncTrack"]))
    ticks=sorted(changes)
    times=[base]
    steps=[60000/(resolution*changes[tick]) for tick in ticks]
    for i in range(1,len(ticks)): times.append(times[-1]+(ticks[i]-ticks[i-1])*steps[i-1])
    def seconds(tick):
        i=bisect_right(ticks,tick)-1
        return times[i]+(tick-ticks[i])*steps[i]
    tracks={}
    last_tick=0
    for key,name,lanes in (("easy","Easy",3),("medium","Medium",4),("hard","Expert",5)):
        rows=[]
        for t,k,d in re.findall(r"(\d+)\s*=\s*N\s+(\d+)\s+(\d+)",sections[name+"Single"]):
            tick,lane,length=int(t),int(k),int(d)
            if lane in (5,6): continue  # Tap/HOPO modifiers on the same gem.
            if lane>=lanes: raise ValueError("Tecla fora desta edição de guitarra.")
            rows.append((tick,lane,seconds(tick),seconds(tick+length)))
            last_tick=max(last_tick,tick+length)
        tracks[key]=sorted(rows)
    phrases=[(seconds(int(t)),s) for t,s in re.findall(r'(\d+)\s*=\s*E\s+"section ([^"]+)"',sections["Events"])]
    beats=[seconds(t) for t in range(0,last_tick+1,resolution)]
    return tracks,phrases,beats,float(np.median(list(changes.values())))/1000


def authored_map(item,clock,duration):
    from analysis_engine import VERSION
    contents=chart_text()
    if contents is None: return None
    tracks,phrases,beats,bpm=parse(contents)
    scale,shift=clock
    def stamp(t): return round(t*scale+shift,4)
    common=set.intersection(*({r[0] for r in rows} for rows in tracks.values()))
    sync=set()
    next_sync=3.
    for tick,_,start,_ in tracks["easy"]:
        if tick in common and stamp(start)>=next_sync:
            sync.add(tick);next_sync=stamp(start)+8.
    charts={}
    for mode,rows in tracks.items():
        notes=[]
        for tick,lane,at,end in rows:
            t=stamp(at)
            if t<0 or t>=duration: return None
            second=bool(notes and notes[-1]["t"]==t)
            n=dict(t=t,lane=lane,part="lead",sync=tick in sync and not second)
            if second:n["chord"]=True
            if end>at:n["duration"]=round(min(duration,stamp(end))-t,4)
            notes.append(n)
        charts[mode]=notes
    names={"Clean Intro":"Introdução limpa","Distortion Starts":"Entrada da distorção",
           "Third Guitar Enters":"Terceira guitarra","Strummy Interlude":"Interlúdio de acordes",
           "Forsaken":"Ponte","Final Chorus":"Refrão final","Interlude":"Interlúdio","Verse":"Verso","Chorus":"Refrão"}
    spans=[]
    for t,name in phrases:
        label=names.get(name) or " ".join(names.get(w,w) for w in name.split())
        start=max(0,stamp(t)) if spans else 0.
        if spans: spans[-1]["end"]=start
        spans.append(dict(start=start,end=round(duration,4),role="lead",label="Guitarra · "+label))
    return dict(duration=round(duration,4),bpm=round(bpm/scale,1),beats=[stamp(t) for t in beats if 0<=stamp(t)<duration],
                waveform=item["waveform"],charts=charts,engine="Guitarra de Wagsii · Clone Hero",analysisVersion=VERSION,
                difficultyLabels={"hard":"Expert · Wagsii","medium":"Médio · Wagsii","easy":"Fácil · Wagsii"},
                performance=dict(id=item["id"],revision=12,sections=spans,credit="Wagsii",chartUrl="https://www.enchor.us/?hash=7229be4a1730a8077687ff088e5547ef"))
