import { createGpuNotes } from "./gpu-notes.js";
const special = {
  heal: { color: "#8dd6a2", label: "Cura" },
  critical: { color: "#ffd16c", label: "Crítico" },
  boost: { color: "#bda7ff", label: "Multiplicador" },
  sync: { color: "#8acfdc", label: "Sincronia" },
};

export function noteKind(note) {
  return note.kind || (note.sync ? "sync" : "normal");
}

export function noteFeedback(note) {
  return special[noteKind(note)]?.label;
}

// Each glyph is drawn once. The frame loop only blits the small visible set.
function makeSprite(color, kind) {
  const tile = document.createElement("canvas");
  tile.width = tile.height = 68;
  const ctx = tile.getContext("2d");
  ctx.translate(34, 34);
  if (kind !== "normal") {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(0, 0, 30, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(0, 0, 24, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#1a221d";
  ctx.strokeStyle = "#1a221d";
  ctx.lineWidth = 4;
  ctx.lineJoin = ctx.lineCap = "round";
  ctx.beginPath();
  if (kind === "heal") {
    ctx.moveTo(-11, 0);
    ctx.lineTo(11, 0);
    ctx.moveTo(0, -11);
    ctx.lineTo(0, 11);
    ctx.stroke();
  } else if (kind === "critical") {
    ctx.moveTo(3, -16);
    ctx.lineTo(-10, 2);
    ctx.lineTo(-2, 2);
    ctx.lineTo(-4, 16);
    ctx.lineTo(11, -4);
    ctx.lineTo(3, -4);
    ctx.closePath();
    ctx.fill();
  } else if (kind === "boost") {
    ctx.moveTo(-10, 1);
    ctx.lineTo(0, -9);
    ctx.lineTo(10, 1);
    ctx.moveTo(-10, 11);
    ctx.lineTo(0, 1);
    ctx.lineTo(10, 11);
    ctx.stroke();
  } else if (kind === "sync") {
    ctx.moveTo(0, -13);
    ctx.lineTo(11, 0);
    ctx.lineTo(0, 13);
    ctx.lineTo(-11, 0);
    ctx.closePath();
    ctx.stroke();
  }
  return tile;
}

export function createRail({
  stage,
  notes,
  keys,
  colors,
  lead,
  coop,
  beats = [],
}) {
  const base = stage.querySelector(".rail-base");
  const canvas = stage.querySelector("#note-layer");
  const background = base.getContext("2d", { alpha: false });
  const ctx = canvas.getContext("2d");
  const count = keys.length;
  const fractions = Array.from(
    { length: count },
    (_, i) => (i + 0.5) / count - 0.5,
  );
  const normal = colors.map((c) => makeSprite(c, "normal"));
  const symbols = Object.fromEntries(
    Object.entries(special).map(([k, meta]) => [k, makeSprite(meta.color, k)]),
  );
  const powerLight = document.createElement("div");
  const powerLevel = document.createElement("span");
  const powerWake = document.createElement("span");
  powerLight.className = "rail-power";
  powerLight.setAttribute("role", "progressbar");
  powerLight.setAttribute("aria-label", "Energia de poder");
  powerLight.setAttribute("aria-valuemin", "0");
  powerLight.setAttribute("aria-valuemax", "100");
  powerLight.setAttribute("aria-valuenow", "0");
  powerLevel.className = "rail-power-level";
  powerWake.className = "rail-power-wake";
  powerLight.append(powerLevel, powerWake);
  stage.insertBefore(powerLight, canvas);
  const gpu = createGpuNotes(stage, [...normal, ...Object.values(symbols)]);
  stage.dataset.renderer = gpu ? "webgl" : "canvas";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const impacts = Array.from({ length: 24 }, () => ({
    lane: 0,
    start: -10000,
    miss: false,
  }));
  let nextImpact = 0,
    firstVisible = 0;
  let powerActive = false;
  let powerPercent = 0;
  let wakeMotion;
  function setPower(active, energy) {
    const percent = Math.min(100, Math.max(0, Math.round(energy) || 0));
    if (powerPercent !== percent) {
      powerPercent = percent;
      powerLevel.style.transform = `scaleX(${percent / 100})`;
      powerLight.setAttribute("aria-valuenow", percent);
    }
    if (active === powerActive) return;
    powerActive = active;
    powerLight.classList.toggle("active", active);
    if (!active || reduced) return;

    wakeMotion?.cancel();
    wakeMotion = powerWake.animate(
      [
        { transform: "scaleY(0.2)", opacity: 0.9 },
        { transform: "scaleY(0.68)", opacity: 0.64, offset: 0.42 },
        { transform: "scaleY(1)", opacity: 0 },
      ],
      { duration: 220, easing: "ease-out" },
    );
  }
  function impact(lane, miss = false) {
    const burst = impacts[nextImpact++ % impacts.length];
    burst.start = performance.now();
    burst.lane = lane;
    burst.miss = miss;
  }
  let beatIndex = 0;
  const noteTiles = notes.map(
    (n) => (coop && symbols[noteKind(n)]) || normal[n.lane],
  );
  const specialSize = Uint8Array.from(
    notes,
    (n) => coop && !!special[noteKind(n)],
  );
  const lightUntil = new Float64Array(count);
  const activeKeys = new Uint8Array(count);
  let w = 1,
    h = 1,
    top,
    target,
    near,
    far,
    radius,
    mid,
    travel,
    end = 0,
    stopped = false;

  function resize() {
    if (stopped) return;
    const rect = canvas.getBoundingClientRect();
    w = Math.max(1, rect.width);
    h = Math.max(1, rect.height);
    const dpr = Math.min(devicePixelRatio || 1, 2);
    for (const layer of [base, canvas]) {
      layer.width = Math.round(w * dpr);
      layer.height = Math.round(h * dpr);
    }
    background.setTransform(base.width / w, 0, 0, base.height / h, 0, 0);
    ctx.setTransform(canvas.width / w, 0, 0, canvas.height / h, 0, 0);
    top = 17;
    target = h - 52;
    travel = target - top;
    mid = w / 2;
    near = w * 0.84;
    far = near * 0.56;
    radius = Math.min(13.5, (near / count) * 0.19);
    powerLight.style.top = target + "px";
    powerLight.style.insetInline = (w - near) / 2 + "px";

    background.fillStyle = "#1a1c19";
    background.fillRect(0, 0, w, h);
    // Faint trapezoid and diverging strings. The player's eye follows the dots.
    background.fillStyle = "#20231e";
    background.beginPath();
    background.moveTo(mid - far / 2, top);
    background.lineTo(mid + far / 2, top);
    background.lineTo(mid + near / 2, target);
    background.lineTo(mid - near / 2, target);
    background.closePath();
    background.fill();
    const horizon = background.createLinearGradient(0, top, 0, target);
    horizon.addColorStop(0, "#30342b");
    horizon.addColorStop(1, "#52594a");
    background.strokeStyle = horizon;
    background.lineWidth = 1;
    for (const fraction of fractions) {
      background.beginPath();
      background.moveTo(mid + far * fraction, top);
      background.lineTo(mid + near * fraction, target);
      background.stroke();
    }
    background.fillStyle = "#777d6e";
    background.fillRect(mid - near / 2, Math.round(target), near, 1);
    background.textAlign = "center";
    background.font = "600 14px Barlow, sans-serif";
    for (let lane = 0; lane < count; lane++) {
      const x = mid + near * fractions[lane];
      background.fillStyle = "#1c201b";
      background.strokeStyle = colors[lane];
      background.lineWidth = 1.75;
      background.beginPath();
      background.arc(x, target, radius + 2, 0, Math.PI * 2);
      background.fill();
      background.stroke();
      background.fillStyle = "#d8ddcf";
      background.fillText(keys[lane], x, target + 32);
    }
  }

  const observer = new ResizeObserver(resize);
  observer.observe(stage);
  resize();
  document.fonts?.ready.then(resize);
  function laneAt(clientX) {
    const rel =
      (clientX - canvas.getBoundingClientRect().left - mid) / near + 0.5;
    return rel < 0 || rel >= 1 ? -1 : Math.floor(rel * count);
  }
  function illuminate(i, time) {
    lightUntil[i] = time + 100;
  }
  function draw(t, stamp, next, flags, label, message) {
    ctx.clearRect(0, 0, w, h);
    // Beat ticks stay at the edge so notes remain the strongest visual cue.
    ctx.fillStyle = "#575d50";
    while (beatIndex < beats.length && beats[beatIndex] < t) beatIndex++;
    while (beatIndex > 0 && beats[beatIndex - 1] >= t) beatIndex--;
    for (let i = beatIndex; i < beats.length && beats[i] <= t + lead; i++) {
      const at = beats[i];
      const x = 1 - (at - t) / lead,
        depth = x * (0.6 + 0.4 * x);
      const y = top + travel * depth,
        half = (far + (near - far) * depth) / 2;
      ctx.fillRect(mid - half - 3, y, 3, 1);
      ctx.fillRect(mid + half, y, 3, 1);
    }
    for (let i = 0; i < count; i++) {
      if (!activeKeys[i] && stamp >= lightUntil[i]) continue;
      const x = mid + near * fractions[i];
      ctx.fillStyle = colors[i];
      ctx.globalAlpha = 0.26;
      ctx.beginPath();
      ctx.arc(x, target, radius + 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.beginPath();
      ctx.arc(x, target, radius - 1, 0, Math.PI * 2);
      ctx.fill();
    }
    if (end < next) end = next;
    while (end < notes.length && notes[end].t <= t + lead) end++;
    // A held note remains visible even after the head leaves the judgment queue.
    while (
      firstVisible < next &&
      notes[firstVisible].t + (notes[firstVisible].duration || 0) < t - 0.19
    )
      firstVisible++;
    const accelerated = gpu?.begin(w, h, canvas.width / w);
    for (let i = end - 1; i >= firstVisible; i--) {
      const n = notes[i],
        held = flags[i] === 3;
      if (flags[i] && !held) continue;
      const p = held ? 1 : 1 - (n.t - t) / lead;
      if (p < 0 || (!held && n.t < t - 0.17)) continue;
      const d = p * (0.6 + 0.4 * p),
        frac = fractions[n.lane],
        x = mid + (far + (near - far) * d) * frac,
        y = top + travel * d;
      const size = (specialSize[i] ? 38 : 33) * (0.64 + 0.36 * d);
      if (n.duration) {
        const tailP = Math.min(
          1,
          Math.max(0, 1 - (n.t + n.duration - t) / lead),
        );
        const tailD = tailP * (0.6 + 0.4 * tailP),
          tx = mid + (far + (near - far) * tailD) * frac,
          ty = top + travel * tailD;
        const cap = size * 0.31;
        ctx.lineCap = "round";
        ctx.lineWidth = cap;
        ctx.strokeStyle = colors[n.lane];
        ctx.globalAlpha = held ? 0.94 : 0.58;
        ctx.beginPath();
        ctx.moveTo(tx, ty);
        ctx.lineTo(x, y - size * 0.29);
        ctx.stroke();
        ctx.globalAlpha = 1;
        ctx.lineWidth = 1.6;
        ctx.strokeStyle = held ? "#fff2dc" : colors[n.lane];
        ctx.beginPath();
        ctx.moveTo(tx - cap * 0.56, ty);
        ctx.lineTo(tx + cap * 0.56, ty);
        ctx.stroke();
      }
      if (accelerated)
        gpu.sprite(noteTiles[i], x - size * 0.5, y - size * 0.5, size, size);
      else
        ctx.drawImage(noteTiles[i], x - size * 0.5, y - size * 0.5, size, size);
    }
    if (accelerated) gpu.finish();
    for (const burst of impacts) {
      const age = (stamp - burst.start) / 340;
      if (age < 0 || age >= 1) continue;
      const x = mid + near * fractions[burst.lane],
        y = target;
      const color = burst.miss ? "#ff796f" : colors[burst.lane],
        r = radius + 3;
      ctx.globalAlpha = (1 - age) * (burst.miss ? 0.85 : 1);
      ctx.lineWidth = 2.6 * (1 - age) + 0.8;
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.beginPath();
      if (burst.miss) {
        ctx.moveTo(x - r * 0.7, y - r * 0.7);
        ctx.lineTo(x + r * 0.7, y + r * 0.7);
        ctx.moveTo(x + r * 0.7, y - r * 0.7);
        ctx.lineTo(x - r * 0.7, y + r * 0.7);
        ctx.stroke();
        ctx.globalAlpha = (1 - age) * 0.15;
        ctx.fillRect(
          x - (near / count) * 0.36,
          y - 46,
          (near / count) * 0.72,
          70,
        );
      } else {
        const spread = reduced ? 6 : 32;
        ctx.arc(x, y, r + spread * age, 0, Math.PI * 2);
        ctx.stroke();
        if (!reduced)
          for (let k = 0; k < 8; k++) {
            const angle = (k * Math.PI) / 4 + 0.18,
              px = x + Math.cos(angle) * (r + age * 42),
              py = y + Math.sin(angle) * (r + age * 42);
            ctx.beginPath();
            ctx.arc(px, py, 2.5 * (1 - age) + 0.5, 0, Math.PI * 2);
            ctx.fill();
          }
      }
    }
    ctx.globalAlpha = 1;
    ctx.textAlign = "center";
    if (message) {
      ctx.fillStyle = "#191c18eb";
      ctx.fillRect(0, h * 0.34, w, h * 0.21);
      ctx.font = "700 27px Barlow, sans-serif";
      ctx.fillStyle = "#f6f2e5";
      ctx.fillText(message, mid, h * 0.465);
    } else if (label) {
      ctx.font = "600 14px Barlow, sans-serif";
      ctx.fillStyle = "#f1ecde";
      // The feedback sits independently of the note paths.
      ctx.fillText(label, mid, h - 6);
    }
  }
  return {
    canvas,
    activeKeys,
    illuminate,
    laneAt,
    impact,
    setPower,
    draw,
    reset() {
      beatIndex = firstVisible = end = nextImpact = 0;
      lightUntil.fill(0);
      activeKeys.fill(0);
      for (const burst of impacts) burst.start = -10000;
      wakeMotion?.cancel();
      setPower(false, 0);
    },
    destroy() {
      stopped = true;
      observer.disconnect();
      wakeMotion?.cancel();
      gpu?.destroy();
    },
  };
}
