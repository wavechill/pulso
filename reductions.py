"""Keep audible melodic answers when reducing an instrumental chart to fewer keys."""
from bisect import bisect_left, bisect_right
from itertools import groupby


REVISION = 1


def _answer(run, parent, protected, keys, radius):
    """Fit one phrase from a higher difficulty into the existing rhythmic slots."""
    original_lane = run[0]["lane"]
    best, best_score = None, 0
    roles = {row["part"] for row in run}
    for part in ("lead", "bass"):
        if len(roles) == 1 and roles <= {"lead", "bass"} and part not in roles:
            continue
        source = [row for row in parent if row["part"] == part and not row.get("chord")]
        if len(source) < 4:
            continue
        source_times = [note["t"] for note in source]
        proposed = []
        for i, row in enumerate(run):
            t = row["t"]
            # Separate neighboring slots, including when a fast double is played.
            left = max(t-radius, (run[i-1]["t"]+t)/2+.035) if i else t
            right = min(t+radius, (t+run[i+1]["t"])/2-.035) if i+1<len(run) else t
            window = source[bisect_left(source_times,left):bisect_right(source_times,right)]
            choices = [p for p in window if t not in protected or p["t"]==t]
            if not choices:
                proposed.append(row)
                continue
            p = min(choices, key=lambda note: abs(note["t"] - t))
            reduced = dict(p, lane=round(p["lane"]*(keys-1)/keys), sync=row.get("sync", False))
            proposed.append(reduced)
        voiced = [r for r in proposed if r["part"] == part]
        changes = sum(a["lane"] != b["lane"] for a,b in zip(voiced,voiced[1:]))
        distinct = sum(r["lane"] != original_lane for r in voiced)
        if len(voiced) < max(4, .70*len(run)) or changes < 2 or distinct < 2:
            continue
        score = (len(voiced)+changes*.3) / len(run)
        if score > best_score:
            best, best_score = proposed, score
    return best


def refine(chart):
    if chart.get("phrasingRevision", 0) >= REVISION:
        return chart
    chart["phrasingRevision"] = REVISION
    if chart.get("performance"):
        return chart
    charts = chart["charts"]
    beat = 60/max(45, chart.get("bpm") or 120)
    for difficulty, parent_name, keys in (("medium","hard",4), ("easy","medium",3)):
        rows, parent = charts[difficulty], charts[parent_name]
        parent_times = [n["t"] for n in parent]
        protected = {n["t"] for n in charts["easy"]} if difficulty=="medium" else set()
        protected.update(n["t"] for n in rows if n.get("sync"))
        start, changed = 0, False
        for _, group in groupby(rows, key=lambda n:n["lane"]):
            run = list(group)
            if len(run)>=5 and all(b["t"]-a["t"]<=1.7*beat for a,b in zip(run,run[1:])):
                lo, hi = bisect_left(parent_times,run[0]["t"]), bisect_right(parent_times,run[-1]["t"])
                answer = _answer(run,parent[lo:hi],protected,keys,min(.24,.40*beat))
                if answer:
                    rows[start:start+len(run)] = answer
                    changed = True
            start+=len(run)
        # A newly exposed pick releases an earlier held note on that string.
        if changed:
            following = [float("inf")]*keys
            for i in range(len(rows)-1,-1,-1):
                r = rows[i]
                if r.get("duration"):
                    end = following[r["lane"]]
                    if difficulty=="easy" and i+1<len(rows):
                        end = min(end,rows[i+1]["t"])
                    duration = min(r["duration"], end-r["t"]-.075)
                    if duration >= (.46 if difficulty=="easy" else .36):
                        r["duration"] = round(duration,4)
                    else:
                        del r["duration"]
                following[r["lane"]]=r["t"]
    return chart
