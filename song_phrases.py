"""Choose one foreground phrase at a time on the decoded recording's clock."""
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

FPS = 22050 / 256
ROLES = ("voice", "lead", "bass", "drums")
LABELS = {"voice":"Melodia vocal", "lead":"Riff e melodia", "bass":"Baixo", "drums":"Batida"}
REVISION = 11


@dataclass
class Attack:
    t: float
    part: str
    weight: float
    pitch: float = -1
    end: float = 0
    key: int = 0
    chord: int = 0
    outline: bool = False
    impact: float = 0
    accent: str = ""
    focus: float = 0

    @property
    def role(self):
        return self.part if self.part in ROLES else "drums"


def _shift(values, frames):
    return values[np.clip(np.arange(len(values))+frames,0,len(values)-1)]


def _mean(values, width):
    width = min(len(values), max(1, int(width)))
    return np.convolve(values, np.ones(width)/width, "same")


def _median(values, width):
    padded = np.pad(values, width//2, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded,width), axis=1)


def _peaks(curve, threshold, distance):
    times = np.flatnonzero((curve >= threshold) & (curve > _shift(curve,-1)) & (curve >= _shift(curve,1)))
    used = np.zeros(len(curve),bool)
    result = []
    for i in times[np.argsort(-curve[times],kind="stable")]:
        if not used[i]:
            result.append(int(i))
            used[max(0,i-distance+1):i+distance] = True
    return sorted(result)


def _singing(data):
    voice = data["voice_bands"]
    mixed = data["mix_voice_bands"]
    bands = voice[:,1:7]  # Include the vowel/formants, avoid treating hiss as a syllable.
    level = np.linalg.norm(bands,axis=1)
    mix = np.linalg.norm(mixed[:,1:7],axis=1)
    share = level / np.maximum(1,mix)
    floor = max(1.,np.percentile(mix,85)*.035)
    presence = np.clip((share-.17)/.40,0,1) * np.clip((level-floor*.45)/(floor*2.5),0,1)
    confi = _mean(data["voice_pitch_confidence"],3)
    pitch = _median(data["voice_midi"],5)
    # Compare vowel bands across two sides of an articulation. A single
    # trembling partial moves inside a band without starting another syllable.
    loud = np.log1p(bands / np.maximum(.5, np.percentile(mixed[:,1:7],80,axis=0)*.065))
    flux = np.maximum(0,_shift(loud,2)-_shift(loud,-2)) @ [.12,.21,.24,.22,.14,.07]
    body = np.log1p(level/floor)
    rise = np.maximum(0,_shift(body,2)-_shift(body,-2))
    before,after = _shift(pitch,-3),_shift(pitch,4)
    motion = np.abs(after-before)
    settled = (np.abs(_shift(pitch,7)-after) < 1.25) & (np.abs(_shift(pitch,-5)-before) < 1.4)
    turn = np.clip((motion-.85)/2.6,0,1) * settled
    turn *= np.minimum(_shift(confi,-3),_shift(confi,4)) * (motion<11.5)
    attack = (_mean(flux,3)*1.55 + rise*.63 + _mean(turn,3)*.94) * np.maximum(presence,_shift(presence,3))
    candidates = _peaks(attack,.145,8)
    audible=(presence>.48)&(confi>.23)
    entries=np.flatnonzero(audible & np.r_[True,~audible[:-1]])
    for j in entries:
        if j and np.any(audible[max(0,j-15):j]):continue
        if np.count_nonzero(audible[j:j+8])<6:continue
        k=bisect_left(candidates,j-5)
        if k==len(candidates) or candidates[k]>j+6:
            candidates.insert(bisect_left(candidates,j),int(j))
            attack[j]=max(attack[j],.72)
    frames = len(level)
    notes = []
    for i,j in enumerate(candidates):
        high = min(frames,j+10,candidates[i+1] if i+1<len(candidates) else frames)
        if high-j<3: continue
        window = slice(j,high)
        if np.mean(presence[window])<.30: continue
        pitched = (confi[window]>.23) & (presence[window]>.45)
        p = float(np.median(pitch[window][pitched])) if np.any(pitched) else -1
        separation = min(1,np.mean(share[window]))
        strength = .48+min(1.9,attack[j])*1.08+.24*separation+.19*min(1,2*np.mean(confi[window]))
        end = j
        if p>=0:
            limit = min(frames,j+round(FPS*4))
            same = (presence[j:limit]>.24) & (confi[j:limit]>.11) & (np.abs(pitch[j:limit]-p)<1.85)
            lost = np.flatnonzero(np.convolve(np.pad(~same,(0,6)),np.ones(7),mode="valid")>=6)
            lost = lost[lost>=min(5,high-j)]
            end = min(limit, j+int(lost[0])) if len(lost) else limit
        # Vowels can pulse several times during a single held pitch. A second
        # key on the same pitch requires a breath, a new vowel or a real dip.
        if notes and p>=0 and abs(p-notes[-1].pitch)<.85 and (j/FPS-notes[-1].t)<.51:
            prior=round(notes[-1].t*FPS)
            valley=float(np.min(body[prior+2:j+1])) if j>prior+2 else body[j]
            shape=loud[j+1 if j+1<frames else j]-loud[max(prior,j-5)]
            if body[j+1 if j+1<frames else j]-valley<.40 and np.ptp(shape)<.85 and strength<1.85:
                notes[-1].end=max(notes[-1].end,round(end/FPS,4))
                continue
        notes.append(Attack(round(j/FPS,4),"voice",strength,p,
                            round(end/FPS,4) if end-j>.26*FPS else 0))
    return notes,presence,confi


def _instrumental(chart, data):
    easy = {n["t"] for n in chart["charts"]["easy"]}
    medium = {n["t"] for n in chart["charts"]["medium"]}
    attacks = []
    for n in chart["charts"]["hard"]:
        if n.get("chord"):
            if attacks and attacks[-1].t==n["t"]: attacks[-1].chord=n["lane"]-attacks[-1].key
            continue
        t,part = n["t"],n["part"]
        if part not in ("lead","bass"): continue
        strength = .78+.32*(t in medium)+.29*(t in easy)+.23*bool(n.get("duration"))
        attacks.append(Attack(t,part,strength,end=t+n.get("duration",0),key=n["lane"],outline=t in easy))
    old_lead=[e for e in attacks if e.part=="lead"]
    old_times=[e.t for e in old_lead]
    for t,pitch,end,chord,weight,support in data.get("lead_gestures",()):
        cut=bisect_left(old_times,t)
        nearby=old_lead[max(0,cut-1):cut+1]
        prior=min(nearby,key=lambda e:abs(e.t-t)) if nearby else None
        if prior is not None and abs(prior.t-t)<=.047:
            if not prior.focus or weight>prior.weight:
                prior.pitch=pitch;prior.focus=support
                prior.weight=max(prior.weight,weight)
                prior.end=max(prior.end,end)
                prior.chord=prior.chord or chord
        elif support>=.33 and weight>=1.05:
            attacks.append(Attack(t,"lead",weight,pitch,end,key=int(np.clip(round((pitch-42)/7),0,4)),chord=chord,focus=support))
    # The old instrumental chart already chose between instruments on each
    # attack. Recover actual kick and snare hits that a louder guitar masked.
    low,low_mid,mid,high,air = data["drums_flux"].T
    bands = np.column_stack((low+.18*low_mid,.46*low_mid+.38*mid+.16*high,.32*high+.68*air))
    norm = np.maximum(.15,np.percentile(bands,92,axis=0))
    relative = np.clip(bands/norm,0,4)*[1,.96,.51]
    loudness = data["drums_level"]/np.maximum(.011,data["rms"])
    curve = relative.max(axis=1)*np.clip((loudness+.04)/.27,0,1)
    for j in _peaks(curve,.29,6):
        kind = int(np.argmax(relative[j]))
        strength = .51+min(2.6,curve[j])*.57
        share=float(np.max(loudness[j:j+4]))
        attacks.append(Attack(round(j/FPS,4),("kick","snare","hat")[kind],strength,key=(0,2,4)[kind],
                              impact=float(np.clip((share-.18)/.37,0,1))*max(0,strength-.75)))
    return attacks,curve


def _phrases(chart, data, presence, confidence, kit, events):
    size = len(presence)
    period = 60/max(40,chart["bpm"] or 120)
    beats = np.asarray(chart["beats"])
    if len(beats)<2: beats = np.arange(0,chart["duration"]+period,period)
    # Retain local tempo changes and fill gaps in the beat track only for
    # grouping. These interpolated ticks never become playable notes.
    ticks = list(np.arange(beats[0]-period,-.5*period,-period)[::-1])
    for a,b in zip(beats,beats[1:]):
        ticks.append(a)
        if b-a>1.65*period:
            steps = max(2,round((b-a)/period))
            ticks.extend(a+(b-a)*k/steps for k in range(1,steps))
    ticks.extend(np.arange(beats[-1],chart["duration"]+period,period))
    ticks = np.asarray(ticks)
    def level(part):
        return _mean(np.clip(data[part+"_level"]/np.maximum(.012,data["rms"]),0,1),7)
    lead,bass,drums = (level(p) for p in ("lead","bass","drums"))
    prominence = _mean(presence*(.59+.41*np.clip(confidence/.44,0,1)),9)
    width = max(3,round(period*FPS))
    changes = np.abs(_shift(_mean(prominence,width),width//2)-_shift(_mean(prominence,width),-width//2))
    changes += .8*np.abs(_shift(_mean(lead,width),width//2)-_shift(_mean(lead,width),-width//2))
    tick_frames = np.clip(np.rint(ticks*FPS).astype(int),0,size-1)
    phase = max(range(min(4,len(ticks))),key=lambda k: np.mean(changes[tick_frames[k::4]]))
    boundaries = np.unique(np.clip(np.r_[0,ticks[phase::4],chart["duration"]],0,chart["duration"]))
    if len(boundaries)>2 and boundaries[1]<1.2*period: boundaries=np.delete(boundaries,1)
    if len(boundaries)>2 and boundaries[-1]-boundaries[-2]<period: boundaries=np.delete(boundaries,-2)
    spans = list(zip(boundaries[:-1],boundaries[1:]))
    times = np.array([a.t for a in events])
    groups = [events[np.searchsorted(times,a):np.searchsorted(times,b)] for a,b in spans]
    scores = []
    riff_support=[]
    for (a,b),group in zip(spans,groups):
        lo,hi = min(size-1,round(a*FPS)),min(size,round(b*FPS))
        cut = slice(lo,max(lo+1,hi))
        v,l,ba,d = (float(np.mean(x[cut])) for x in (prominence,lead,bass,drums))
        counts = {p:sum(n.role==p for n in group)/max(.25,b-a) for p in ROLES}
        phrased = min(1,counts["voice"]/1.7)
        striking = float(np.mean(np.clip(kit[cut],0,2)))
        punches=[n for n in group if n.part in ("kick","snare") and n.weight>1.45]
        phase=np.interp([n.t for n in punches],ticks,np.arange(len(ticks)))
        groove=min(1,np.count_nonzero(np.abs(2*phase-np.rint(2*phase))<.21)/max(2,(b-a)/period))
        groove *= min(1,3.5*striking)*max(.30,1-.67*v)
        fingering={n.key for n in group if n.part=="lead"}
        riff=np.clip((counts["lead"]-1.9)/2.8,0,1)*min(1,max(0,len(fingering)-1)/2)
        riff*=min(1,l/.28)*(.92-.32*v)
        notes=[n for n in group if n.role=="lead"]
        pitched=[n.focus for n in notes if n.focus]
        # Recognizable, articulated notes may be sparse, as in a fingerpicked
        # intro or a piano reply. Volume and the number of notes miss both.
        focus=(float(np.mean(pitched))*min(1,len(pitched)/max(1,.90*(b-a)))*
               np.clip((l-.08)/.21,0,1)*max(.34,1-.55*v)) if pitched else 0
        texture=min(1.,counts["lead"]/4.6)*min(1.,max(0,len(fingering)-1)/1.8)
        if notes: texture*=.85+.15*min(1,3*sum(n.chord or n.outline for n in notes)/len(notes))
        riff_support.append(np.clip((l-.16)/.16,0,1)*texture*max(0,1-1.25*v))
        scores.append((.06+1.50*v*(.67+.33*phrased),
                       .22+1.42*l+.21*min(1,counts["lead"]/2.8)+.42*riff+.86*focus,
                       .13+1.47*ba+.14*min(1,counts["bass"]/2.6),
                       .17+1.33*d+.61*striking+.10*min(1,counts["drums"]/2.5)+.50*groove))
    # Viterbi on musical bars. Evidence must persist to change the player's
    # part; an isolated loud syllable or snare can still be an accent.
    scores = np.asarray(scores)
    # A riff is often exposed for two bars, then partly covered by its own
    # drum fill. Evidence on both sides keeps the guitar phrase playable.
    # Sparse melodic stabs above an actual drum loop do not earn that weight.
    centered=np.pad(riff_support,(2,2),mode="edge")
    profile=np.convolve(centered,[.12,.21,.34,.21,.12],mode="valid")
    scores[:,1]+=.70*np.clip((profile-.27)/.43,0,1)
    value = scores[0].copy()
    history = []
    for score in scores[1:]:
        paths = value[:,None] - .40*(1-np.eye(4))
        parent = np.argmax(paths,axis=0)
        history.append(parent)
        value = score+paths[parent,np.arange(4)]
    state = int(np.argmax(value)); states=[state]
    for parent in reversed(history):
        state = int(parent[state]); states.append(state)
    roles = [ROLES[i] for i in reversed(states)]
    return spans,groups,roles,ticks


def _recurring(spans,groups,roles):
    """Look for independently heard returns of the same rhythm and contour."""
    features = np.zeros((len(spans),24),float)
    shapes = np.zeros((len(spans),12),float)
    for i,((a,b),group,role) in enumerate(zip(spans,groups,roles)):
        for e in group:
            if e.role!=role: continue
            pos = min(23,round(24*(e.t-a)/(b-a)))
            features[i,pos] = max(features[i,pos], e.weight)
            if e.pitch>=0: shapes[i,round(e.pitch)%12] += e.weight
            elif 0<=e.key<5: shapes[i,e.key] += e.weight
    unit = features/np.maximum(.01,np.linalg.norm(features,axis=1,keepdims=True))
    tonal = shapes/np.maximum(.01,np.linalg.norm(shapes,axis=1,keepdims=True))
    for i,((a,b),group,role) in enumerate(zip(spans,groups,roles)):
        # Bound memory on concert-length recordings. Search the closest 256
        # bars in each direction for a return of this musical pattern.
        lo,hi=max(0,i-256),min(len(spans),i+257)
        sim=.72*np.einsum("ij,j->i",unit[lo:hi],unit[i])+.28*np.einsum("ij,j->i",tonal[lo:hi],tonal[i])
        near = [j for j in lo+np.flatnonzero(sim>.67) if abs(i-j)>=3 and roles[j]==role]
        if not near: continue
        best = sorted(near,key=lambda j:-sim[j-lo])[:4]
        for e in group:
            if e.role!=role: continue
            pos = min(23,round(24*(e.t-a)/(b-a)))
            # Repetitions reinforce a real local attack, never manufacture one.
            agreement = np.mean(np.max(features[best,max(0,pos-1):min(24,pos+2)],axis=1)>.82)
            e.weight += .24*agreement


def _foreground(groups,roles,period,ticks):
    pool = []
    principal=[e for g,role in zip(groups,roles) for e in g if e.role==role]
    main_times = np.array([e.t for e in principal])
    punches=[e for g in groups for e in g if e.part in ("kick","snare") and e.weight>1.34]
    punch_times=np.array([e.t for e in punches])
    # A returning kick/clap is a recognizable event even with uninterrupted
    # singing. Compare the same rhythm position on adjacent bars (including
    # triplets), but require the drum itself to be loud in this recording.
    phases=np.interp(punch_times,ticks,np.arange(len(ticks)))
    for i,punch in enumerate(punches):
        if punch.impact<.46:continue
        lo,hi=np.searchsorted(phases,[phases[i]-16.3,phases[i]+16.3])
        delta=np.abs(phases[lo:hi]-phases[i])
        aligned=(delta>1.6)&(np.abs(delta-2*np.rint(delta/2))<.14)
        similar=sum(punches[lo+k].impact>.46 and punches[lo+k].part==punch.part for k in np.flatnonzero(aligned))
        if similar>=2: punch.impact+=.33
        elif punch.impact<1.05:punch.impact=0
    for group,role in zip(groups,roles):
        for e in group:
            if e.part=="bass" and e.outline and e.end-e.t>=max(.50,.86*period) and role!="voice":
                # A ringing bass downbeat can coexist with the snare that
                # follows it. Play the audible resonance through that answer.
                e.weight+=1.12
                pool.append(e)
                continue
            if e.role == role:
                if role!="drums" and punches:
                    k=np.searchsorted(punch_times,e.t)
                    accent=min(punches[max(0,k-1):k+1],key=lambda p:abs(p.t-e.t))
                    delta=accent.t-e.t
                    if abs(delta)<.062:
                        agreement=1-abs(delta)/.062
                        e.weight+=.38*agreement
                        if role!="voice" and not e.focus: e.t=round(e.t+.62*agreement*delta,4)
                pool.append(e)
                continue
            k = np.searchsorted(main_times,e.t)
            distance = min(abs(t-e.t) for t in main_times[max(0,k-1):k+1]) if len(main_times) else 10
            if e.part in ("kick","snare") and e.impact>=.96:
                if distance<.068:
                    p=min(principal[max(0,k-1):k+1],key=lambda v:abs(v.t-e.t))
                    if p.role!="drums":
                        p.accent=e.part
                        p.weight+=.33*e.impact
                else:
                    e.weight+=.77*e.impact
                    pool.append(e)
                continue
            # Give the featured voice/guitar time to answer. Strong drum
            # punctuation is allowed between syllables even in a sung bar.
            accent = e.part in ("kick","snare") and e.weight>=1.22
            gap = (max(.145,.37*period) if role=="lead" else max(.20,.50*period)) if accent else max(.39,.98*period)
            if e.part=="lead" and e.focus>=.52:
                gap=min(gap,max(.18,.48*period))
            if distance>=gap and (accent or (e.weight>=1.06 and e.part!="hat")):
                e.weight *= .87 if accent else .79
                pool.append(e)
    return sorted(pool,key=lambda e:e.t)


def _articulation_clock(pool,ticks):
    """Place vocal attacks at perceptual onset on locally pulse-locked recordings."""
    if len(ticks)<3: return
    ids=[i for i,e in enumerate(pool) if e.part=="voice"]
    if not ids: return
    t=np.array([pool[i].t for i in ids])
    indices=np.arange(len(ticks))
    phase=np.interp(t,ticks,indices)
    for lo in range(0,len(ticks),16):
        inside=(phase>=lo)&(phase<lo+16)
        near=(phase>=lo-8)&(phase<lo+24)&(t>=ticks[0])&(t<=ticks[-1])
        if np.count_nonzero(near)<8: continue
        best=None
        for subdivisions in (2,3):
            cycle=phase[near]*subdivisions
            lock=np.mean(np.exp(2j*np.pi*cycle))
            coherence=abs(lock) - (.09 if subdivisions==3 else 0)
            if best is None or coherence>best[0]: best=(coherence,subdivisions,np.angle(lock)/(2*np.pi))
        coherence,div,offset=best
        if coherence<.34:continue
        rows=np.flatnonzero(inside)
        target=np.interp(np.rint(phase[rows]*div-offset)/div,indices,ticks)
        # Some singers deliberately phrase behind the drums. Limit the
        # correction to half an analysis window on freely placed syllables.
        for j,goal in zip(rows,target):
            delta=t[j]-goal
            if abs(delta)>.062 or t[j]<ticks[0] or t[j]>ticks[-1]: continue
            weight=np.clip((coherence-.22)/.44,0,.88)*np.clip((.067-abs(delta))/.025,0,1)
            pool[ids[j]].t=round(t[j]-weight*delta,4)
    pool.sort(key=lambda e:e.t)


def _levels(pool, ticks, bpm):
    period = 60/max(40,bpm or 120)
    clock = np.interp([e.t for e in pool],ticks,np.arange(len(ticks)))
    onbeat = np.exp(-.5*((clock-np.rint(clock))/.12)**2)
    half = np.exp(-.5*((2*clock-np.rint(2*clock))/.18)**2)
    # Inherit the exact attacks. Removing embellishments must never move a
    # syllable onto a different beat on an easier difficulty.
    t = np.array([e.t for e in pool])
    allowed = np.arange(len(pool))
    result = {}
    for name,threshold,gap,metric in (("hard",.80,np.clip(.185*period,.079,.112),.10),
                                       ("medium",1.15,np.clip(.42*period,.148,.225),.38),
                                       ("easy",1.29,np.clip(.80*period,.26,.42),.50)):
        scores = np.array([e.weight+.12*e.outline+.12*(e.end-e.t>.35) for e in pool])
        scores += metric*(.76*onbeat+.24*half-.34)
        if name!="hard":
            scores-=np.array([(.43 if name=="medium" else .68)*(1-.72*onbeat[i]-.28*half[i])
                              if e.part=="voice" else 0 for i,e in enumerate(pool)])
        # A quiet but clearly articulated phrase still gets a head. Loud bars
        # have no obligation to fill the rail, and real rests stay empty.
        anchors = []
        times=t[allowed]
        for i in range(0,len(ticks)-1,4):
            lo,hi=np.searchsorted(times,[ticks[i]-.055,ticks[min(i+4,len(ticks)-1)]-.055])
            chunk=allowed[lo:hi]
            if len(chunk): anchors.append(chunk[np.argmax(scores[chunk])])
        candidates = np.union1d(allowed[scores[allowed]>=threshold],np.array(anchors,int))
        if len(allowed) and not len(candidates): candidates=allowed[[np.argmax(scores[allowed])]]
        occupied=np.zeros(len(t),bool)
        chosen = []
        for j in candidates[np.argsort(-scores[candidates],kind="stable")]:
            if occupied[j]: continue
            chosen.append(j)
            occupied[np.searchsorted(t,t[j]-gap,side="right"):np.searchsorted(t,t[j]+gap)] = True
        allowed = np.array(sorted(chosen),int)
        result[name] = allowed
    return result


def _keys(pool, lanes):
    # Read a contour on a finite keyboard. Ranking all pitches globally alone
    # would collapse the low opening of a high chorus onto a single key.
    result=[round(e.key*(lanes-1)/4) for e in pool]
    for part in ("voice","lead"):
        indices=[i for i,e in enumerate(pool) if e.part==part and (part=="voice" or e.pitch>=0)]
        if indices: _contour_keys(pool,indices,lanes,result)
    return result


def _contour_keys(pool,indices,lanes,result):
    pitches = [pool[i].pitch for i in indices if pool[i].pitch>=0]
    thresholds = np.quantile(pitches,np.arange(1,lanes)/lanes) if pitches else []
    keys=np.arange(lanes)
    boundaries=[0]+[i for i in range(1,len(indices)) if pool[indices[i]].t-pool[indices[i-1]].t>1.2]+[len(indices)]
    for a,b in zip(boundaries,boundaries[1:]):
        if a==b:continue
        group=indices[a:b]
        p=np.array([pool[i].pitch for i in group])
        voiced=np.flatnonzero(p>=0)
        if not len(voiced):
            for i in group: result[i]=lanes//2
            continue
        values=np.rint(np.interp(np.arange(len(p)),voiced,p[voiced])).astype(int)
        anchor=np.searchsorted(thresholds,values)
        score=.18*(keys-anchor[0])**2; path=[]
        jumps=keys[None,:]-keys[:,None]
        for j,diff in enumerate(np.diff(values),1):
            step=np.sign(diff)*min(lanes-1,max(1,round(abs(diff)/3.2))) if diff else 0
            mismatch=(jumps-step)**2*.65
            if diff: mismatch+=1.3*(np.sign(jumps)!=np.sign(diff))
            else: mismatch+=2.1*(jumps!=0)
            cost=score[:,None]+mismatch
            parent=cost.argmin(axis=0);path.append(parent)
            score=cost[parent,keys]+.18*(keys-anchor[j])**2
        lane=int(score.argmin())
        for j in reversed(range(len(group))):
            result[group[j]]=lane
            if j:lane=int(path[j-1][lane])


def _ensemble_riffs(chart,spans,roles,presence,confidence):
    """Keep the full band's articulation when bass and guitar drive the same fast riff."""
    notes=[e for e in chart["charts"]["hard"] if not e.get("chord")]
    times=[n["t"] for n in notes]
    seeds=[];continues=[]
    for (a,b),role in zip(spans,roles):
        group=notes[bisect_left(times,a):bisect_left(times,b)]
        length=max(.25,b-a)
        pitched=sum(n["part"] in ("bass","lead") for n in group)/length
        guitar=sum(n["part"]=="lead" for n in group)/length
        cut=slice(round(a*FPS),max(round(a*FPS)+1,round(b*FPS)))
        sung=np.mean(presence[cut]*np.minimum(1,confidence[cut]/.42))
        seeds.append(role=="lead" and sung<.16 and pitched>=5.8 and guitar>=4.0)
        continues.append(role!="voice" and sung<.61 and pitched>=3.85 and guitar>=2.3)
    keep=np.zeros(len(spans),bool)
    active=False
    for i,strong in enumerate(seeds):
        if strong and i+1<len(spans) and seeds[i+1]:active=True
        if not continues[i]:active=False
        keep[i]=active
    return keep


def _join_riffs(generated,original,spans,keep,duration):
    ends=np.asarray([b for _,b in spans])
    def inside(t):return keep[min(len(ends)-1,int(np.searchsorted(ends,t,side="right")))]
    retained={m:[dict(n) for n in rows if inside(n["t"])] for m,rows in original.items()}
    protected=np.array([n["t"] for n in retained["hard"]])
    for mode,rows in generated.items():
        merged=[]
        for n in rows:
            t=n["t"]
            if inside(t):continue
            k=np.searchsorted(protected,t)
            if any(abs(x-t)<.078 for x in protected[max(0,k-1):k+1]):continue
            merged.append(n)
        rows[:]=sorted(merged+retained[mode],key=lambda n:n["t"])
        next_key={}
        for i in reversed(range(len(rows))):
            n=rows[i];t=n["t"];lane=n["lane"]
            if "duration" in n:
                end=min(duration,next_key.get(lane,duration+.07)-.07)
                if mode=="easy" and i+1<len(rows):end=min(end,rows[i+1]["t"]-.05)
                length=round(min(n["duration"],end-t),4)
                if length>=.29:n["duration"]=length
                else:del n["duration"]
            next_key[lane]=t
    sync={n["t"] for n in generated["easy"] if n["sync"]}
    if not sync and generated["easy"]:
        chord_times={n["t"] for n in generated["hard"] if n.get("chord")}
        sync.update(n["t"] for n in generated["easy"] if n["t"] not in chord_times)
        if sync: sync={min(sync)}
    for rows in generated.values():
        for n in rows:n["sync"]=n["t"] in sync and not n.get("chord",False)


def arrange(chart,data):
    """Arrange an unreviewed recording. All difficulties share one performance."""
    voices,presence,confidence = _singing(data)
    instrumental,kit = _instrumental(chart,data)
    events=sorted(voices+instrumental,key=lambda e:e.t)
    if not events: return chart
    spans,groups,roles,ticks = _phrases(chart,data,presence,confidence,kit,events)
    original=chart["charts"].copy()
    ensemble=_ensemble_riffs(chart,spans,roles,presence,confidence)
    _recurring(spans,groups,roles)
    period=60/max(40,chart["bpm"] or 120)
    pool=_foreground(groups,roles,period,ticks)
    if not pool: return chart
    _articulation_clock(pool,ticks)
    following={}
    for e in reversed(pool):
        if e.part in ("voice","bass"):
            e.end=min(e.end,following.get(e.part,chart["duration"])-.032)
            following[e.part]=e.t
    levels=_levels(pool,ticks,chart["bpm"])
    easy=levels["easy"]
    shared=set()
    buckets=defaultdict(list)
    for i in easy: buckets[int(np.searchsorted(ticks,pool[i].t))//16].append(i)
    for group in buckets.values(): shared.add(max(group,key=lambda i:(pool[i].end-pool[i].t<.3,pool[i].weight)))
    for mode,lanes in (("easy",3),("medium",4),("hard",5)):
        selected=[pool[j] for j in levels[mode]]
        keys=_keys(selected,lanes)
        if mode!="easy":
            times=[e.t for e in selected]
            for i,e in enumerate(selected):
                if e.part!="bass" or e.end-e.t<.32:continue
                stop=bisect_left(times,e.end+.07,lo=i+1)
                busy={keys[j] for j in range(i+1,stop) if selected[j].role=="drums"}
                if keys[i] in busy:
                    free=[lane for lane in range(lanes) if lane not in busy]
                    if free:keys[i]=min(free,key=lambda lane:abs(lane-keys[i]))
        chart_notes=[]
        for i,j in enumerate(levels[mode]):
            e=pool[j]; key=keys[i]
            chord=min(e.chord,lanes-1) if mode=="hard" and j not in shared and e.weight>=1.31 else 0
            key=min(key,lanes-1-chord)
            row=dict(t=e.t,lane=key,part=e.part,sync=j in shared)
            if e.accent:row["accent"]=e.accent
            if e.end>e.t: row["duration"]=e.end-e.t
            chart_notes.append(row)
            if chord: chart_notes.append(dict(t=e.t,lane=key+chord,part=e.part,sync=False,chord=True))
        following=[chart["duration"]+.07]*lanes
        for i in reversed(range(len(chart_notes))):
            n=chart_notes[i]; t=n["t"]; lane=n["lane"]
            if "duration" in n:
                end=following[lane]-.07
                if mode=="easy" and i+1<len(chart_notes): end=min(end,chart_notes[i+1]["t"]-.055)
                length=min(n["duration"],end-t,chart["duration"]-t)
                if length>=max(.30,period*.84): n["duration"]=round(length,4)
                else: del n["duration"]
            following[lane]=t
        chart["charts"][mode]=chart_notes
    if np.any(ensemble):
        _join_riffs(chart["charts"],original,spans,ensemble,chart["duration"])
        roles=["lead" if preserve else role for preserve,role in zip(ensemble,roles)]
    sections=[]
    for (start,end),role in zip(spans,roles):
        label=LABELS[role]
        if role=="lead" and "lead_families" in data:
            from instrument_focus import frame_index,FAMILIES
            values=data["lead_families"]
            a,b=frame_index([start,end],len(values))
            evidence=values[a:b+1].mean(axis=0)
            best=int(evidence.argmax())
            if evidence[best]>.38 and evidence[best]-np.partition(evidence,-2)[-2]>.10:
                label=FAMILIES[best]
        if sections and sections[-1]["label"]==label: sections[-1]["end"]=round(float(end),4)
        else: sections.append(dict(start=round(float(start),4),end=round(float(end),4),role=role,label=label))
    medium=chart["charts"]["medium"]
    stamps=[e["t"] for e in medium]
    for s in sections:
        if s["role"]=="voice":
            notes=medium[bisect_left(stamps,s["start"]):bisect_left(stamps,s["end"])]
            beats=sum(n.get("accent") in ("kick","snare") or n["part"] in ("kick","snare") for n in notes)
            if beats>=3 and beats>=.16*len(notes): s["label"]="Voz e batida"
    chart["performance"]={"id":"frases","revision":REVISION,"sections":sections}
    return chart
