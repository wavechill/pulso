import { createGpuNotes } from "./gpu-notes.js";
const special = {
  heal: { color: "#39ff83", label: "Cura" },
  critical: { color: "#ffbf00", label: "Crítico" },
  boost: { color: "#cd43ff", label: "Multiplicador" },
  sync: { color: "#00e8ff", label: "Sincronia" },
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
  const halo=ctx.createRadialGradient(0,0,14,0,0,34);
  halo.addColorStop(0,color+"88"); halo.addColorStop(.73,color+"70"); halo.addColorStop(1,color+"00");
  ctx.fillStyle=halo;ctx.fillRect(-34,-34,68,68);
  if (kind !== "normal") {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(0, 0, 30, 0, Math.PI * 2);
    ctx.stroke();
  }
  // The elliptical rim, bevel and enamel all share one cached GPU tile.
  ctx.fillStyle = "#090b12";
  ctx.beginPath();
  ctx.ellipse(0, 7, 29, 16, 0, 0, Math.PI * 2);
  ctx.fill();
  const bevel = ctx.createLinearGradient(0, -12, 0, 17);
  bevel.addColorStop(0, "#fff4dd");
  bevel.addColorStop(.39, color);
  bevel.addColorStop(.72, "#242132");
  bevel.addColorStop(1, color);
  ctx.fillStyle = bevel;
  ctx.beginPath();
  ctx.ellipse(0, 3, 28, 16, 0, 0, Math.PI * 2);
  ctx.fill();
  const face = ctx.createRadialGradient(-5, -8, 2, 0, 0, 26);
  face.addColorStop(0, "#fff9e7");
  face.addColorStop(.18, color);
  face.addColorStop(.87, color);
  face.addColorStop(1, "#332632");
  ctx.fillStyle = face;
  ctx.beginPath();
  ctx.ellipse(0, -1, 24, 15, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.lineWidth = 2.2;
  ctx.strokeStyle = "#fff4df60";
  ctx.beginPath();
  ctx.ellipse(0, -2, 20, 10.2, 0, Math.PI * 1.08, Math.PI * 1.87);
  ctx.stroke();
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

// A tiny original flame atlas also works on the software renderer.
function makeFlames() {
  return Array.from({length: 8}, (_, phase) => {
    const tile = document.createElement("canvas");
    tile.width = 72; tile.height = 100;
    const c = tile.getContext("2d"), angle = phase * Math.PI / 4;
    const light = c.createRadialGradient(36, 77, 2, 36, 77, 36);
    light.addColorStop(0,"#fff39fe6"); light.addColorStop(.37,"#ff8510ad");
    light.addColorStop(1,"#ff4d0000");
    c.fillStyle = light;
    c.fillRect(0, 0, 72, 100);
    for (let i = 0; i < 4; i++) {
      const x = [25,45,35,34][i], width = [12,11,13,8][i];
      const height = [43,48,62,32][i] + Math.sin(angle + i*2)*8;
      const tip = x + Math.sin(angle+i*1.7)*9;
      const fill=c.createLinearGradient(0,81-height,0,84);
      fill.addColorStop(0,["#ff542700","#ff350500","#ff761c39","#ffe477a0"][i]);
      fill.addColorStop(.36,i===3?"#fff1a1":"#ff7e18");
      fill.addColorStop(1,i>=2?"#fffbd5":"#ffcf39");
      c.fillStyle=fill; c.beginPath(); c.moveTo(x-width,79);
      c.bezierCurveTo(x-width-3,65,tip+9,68-height*.57,tip,81-height);
      c.bezierCurveTo(tip+width,74-height*.48,x+width+6,65,x+width,79);
      c.quadraticCurveTo(x,89,x-width,79); c.fill();
    }
    for (let i=0;i<4;i++) {
      const age=((phase+i*2)%8)/8;
      c.globalAlpha=1-age;
      c.fillStyle=i%2?"#fff1a0":"#ff9d40";
      c.fillRect(20+i*10+Math.sin(angle+i)*4,57-age*47,1.8,3.8);
    }
    return tile;
  });
}

function makeLightning(width, height, color, variant) {
  const tile = document.createElement("canvas");
  tile.width = Math.ceil(width); tile.height = Math.ceil(height);
  const c = tile.getContext("2d");
  c.translate(width*.5,0);
  const main = [[.045,0],[-.065,.18],[-.023,.17],[-.088,.35],[.012,.32],
    [-.041,.54],[.066,.51],[-.039,.74],[-.008,.729],[0,.989]];
  const forks = [
    [main[3],[-.13,.38],[-.175,.39],[-.21,.46],[-.27,.49]],
    [main[6],[.18,.42],[.164,.455],[.28,.36]],
    [main[7],[-.13,.67],[-.18,.69],[-.26,.61]],
  ];
  let seed=1619+273*variant;
  function stroke(points, weight, fork=false) {
    const path=new Path2D();
    path.moveTo(points[0][0]*width,points[0][1]*height);
    for (let i=1;i<points.length;i++) {
      const [x,y]=points[i], [px,py]=points[i-1];
      const steps=Math.max(1,Math.round(Math.hypot((x-px)*width,(y-py)*height)/11));
      for(let j=1;j<=steps;j++) {
        seed=(Math.imul(seed,1664525)+1013904223)>>>0;
        const drift=j===steps?0:((seed/4294967296)-.5)*(fork?3.4:4.8);
        path.lineTo((px+(x-px)*j/steps)*width+drift,(py+(y-py)*j/steps)*height);
      }
    }
    c.lineJoin=c.lineCap="round";
    for (const [scale,ink] of [[8,color+"30"],[4,color+"9c"],[2,color],[.95,"#ffffff"]]) {
      c.lineWidth=weight*scale; c.strokeStyle=ink; c.stroke(path);
    }
  }
  for(const fork of forks) stroke(fork,1,true);
  stroke(main,2.55);
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
  const background = base.getContext("2d");
  const ctx = canvas.getContext("2d");
  const count = keys.length;
  const fractions = Array.from(
    { length: count },
    (_, i) => (i + 0.5) / count - 0.5,
  );
  const normal = colors.map((c) => makeSprite(c, "normal"));
  const flames = makeFlames();
  let lightning;
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
  let powerFlash = -Infinity;
  let streakStart = -Infinity;
  let streakLane = 0;
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
    powerFlash = performance.now();

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
    if (rect.width < 2 || rect.height < 2) return;
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
    far = near * 0.44;
    radius = Math.min(13.5, (near / count) * 0.19);
    powerLight.style.top = target + "px";
    powerLight.style.insetInline = (w - near) / 2 + "px";

    background.clearRect(0, 0, w, h);
    lightning = colors.map((c,i)=>makeLightning(Math.max(24,near/count*1.15),Math.max(40,travel*.85),c,i));
    // Draw the beveled sides and graded road surface once per resize.
    const road = background.createLinearGradient(0, top, 0, target);
    road.addColorStop(0, "#14131cee");
    road.addColorStop(.53, "#23232bf7");
    road.addColorStop(1, "#302b32");
    background.fillStyle = road;
    background.beginPath();
    background.moveTo(mid - far / 2, top);
    background.lineTo(mid + far / 2, top);
    background.lineTo(mid + near / 2, target);
    background.lineTo(mid - near / 2, target);
    background.closePath();
    background.fill();
    background.save();
    background.clip();
    // Stamped chevrons and fine diagonal grooves catch light across the metal.
    for(let i=1;i<24;i++) {
      const d=(i/24)**1.45, y=top+travel*d, half=(far+(near-far)*d)/2;
      const dip=4+13*d;
      for (const [offset,shade] of [[0,"#00000050"],[1.5,"#eee6ff0e"]]) {
        background.strokeStyle=shade; background.lineWidth=1+d;
        background.beginPath();background.moveTo(mid-half,y+offset-dip);
        background.lineTo(mid,y+offset);background.lineTo(mid+half,y+offset-dip);background.stroke();
      }
    }
    background.strokeStyle="#dad9ff06";background.lineWidth=.6;
    for(let x=-h;x<w;x+=7){
      background.beginPath(); background.moveTo(x,top); background.lineTo(x+h,target); background.stroke();
    }
    background.restore();
    for (const side of [-1, 1]) {
      const start = mid + side * far / 2;
      const finish = mid + side * near / 2;
      background.fillStyle = "#080911";
      background.beginPath();
      background.moveTo(start, top);
      background.lineTo(start + side * 3, top + 5);
      background.lineTo(finish + side * 10, target + 9);
      background.lineTo(finish, target);
      background.closePath();
      background.fill();
      background.lineWidth = 1.2;
      background.strokeStyle = "#82808d";
      background.beginPath();
      background.moveTo(start, top);
      background.lineTo(finish, target);
      background.stroke();
    }
    const horizon = background.createLinearGradient(0, top, 0, target);
    horizon.addColorStop(0, "#4a4651");
    horizon.addColorStop(1, "#787080");
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
      background.fillStyle = "#141019";
      background.strokeStyle = colors[lane];
      background.lineWidth = 2.6;
      background.beginPath();
      background.ellipse(x,target,radius+5,radius*.76+2,0,0,Math.PI*2);
      background.fill();
      background.stroke();
      background.lineWidth = 1;
      background.strokeStyle = "#e7f0dc3a";
      background.beginPath();
      background.ellipse(x,target-1,radius+1,radius*.62,0,Math.PI*1.08,Math.PI*1.92);
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
    // Subtle crossbars give the projected road a readable beat and depth.
    while (beatIndex < beats.length && beats[beatIndex] < t) beatIndex++;
    while (beatIndex > 0 && beats[beatIndex - 1] >= t) beatIndex--;
    for (let i = beatIndex; i < beats.length && beats[i] <= t + lead; i++) {
      const at = beats[i];
      const x = 1 - (at - t) / lead,
        depth = x * (0.6 + 0.4 * x);
      const y = top + travel * depth,
        half = (far + (near - far) * depth) / 2;
      ctx.fillStyle = i % 4 === 0 ? "#e7dfff53" : "#e7dfff24";
      ctx.fillRect(mid - half, y, half * 2, i % 4 === 0 ? 2 : 1);
      ctx.fillStyle = "#8f8799";
      ctx.fillRect(mid - half, y, 3, 1);
      ctx.fillRect(mid + half - 3, y, 3, 1);
    }
    for (let i = 0; i < count; i++) {
      if (!activeKeys[i] && stamp >= lightUntil[i]) continue;
      const x = mid + near * fractions[i];
      ctx.fillStyle = colors[i];
      ctx.globalAlpha = 0.26;
      ctx.beginPath();
      ctx.ellipse(x,target,radius+7,radius*.83+3,0,0,Math.PI*2);
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.beginPath();
      ctx.ellipse(x,target,radius+2,radius*.68,0,0,Math.PI*2);
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
      // Keep notes still in the late hit window clear of the key labels.
      const p = held ? 1 : Math.min(1, 1 - (n.t - t) / lead);
      if (p < 0 || (!held && n.t < t - 0.17)) continue;
      const d = p * (0.6 + 0.4 * p),
        frac = fractions[n.lane],
        x = mid + (far + (near - far) * d) * frac,
        y = top + travel * d;
      const size = (specialSize[i] ? 42 : 39) * (0.64 + 0.36 * d);
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
      if (held && !reduced) {
        const tile=flames[(Math.floor(stamp/48)+n.lane)%flames.length];
        ctx.drawImage(tile,x-21,target-49,42,60);
      }
    }
    if (accelerated) gpu.finish();
    const boltAge = (stamp - streakStart) / 430;
    if (boltAge >= 0 && boltAge < 1 && lightning) {
      const x=mid+near*fractions[streakLane],bolt=lightning[streakLane];
      ctx.save();
      ctx.globalAlpha = Math.min(1,(1-boltAge)*1.6);
      ctx.drawImage(bolt,x-bolt.width/2,target-bolt.height);
      ctx.globalAlpha=(1-boltAge)*.9;
      ctx.strokeStyle=colors[streakLane];ctx.lineWidth=3*(1-boltAge)+1;
      ctx.beginPath();ctx.ellipse(x,target,12+near/count*.62*boltAge,3+18*boltAge,0,0,Math.PI*2);ctx.stroke();
      ctx.restore();
    }
    const flash = (stamp - powerFlash) / 330;
    if (flash >= 0 && flash < 1) {
      // The activation spark rises from the strike line and clears the lane.
      const size = 50 + flash * 18;
      ctx.globalAlpha = 1 - flash;
      ctx.drawImage(symbols.critical, mid - size / 2, target - size - flash * 30, size, size);
      ctx.globalAlpha = 1;
    }
    for (const burst of impacts) {
      const age = (stamp - burst.start) / (burst.miss ? 220 : 340);
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
        const cross = r * 0.46;
        ctx.arc(x, y, r - 1, 0, Math.PI * 2);
        ctx.moveTo(x - cross, y - cross);
        ctx.lineTo(x + cross, y + cross);
        ctx.moveTo(x + cross, y - cross);
        ctx.lineTo(x - cross, y + cross);
        ctx.stroke();
      } else {
        const spread = reduced ? 4 : 18;
        ctx.ellipse(x,y,r+spread*age,(r+spread*age)*.62,0,0,Math.PI*2);
        ctx.stroke();
        if (!reduced) {
          if (age<.54) {
            const bolt=lightning[burst.lane],width=bolt.width*.78;
            const height=Math.min(travel*.38,125)*(1-.16*age);
            ctx.globalAlpha=Math.max(0,1-age/.54);
            ctx.drawImage(bolt,x-width/2,target-height,width,height);
            ctx.globalAlpha=1-age;
          }
          const size=53+14*Math.sin(age*Math.PI);
          ctx.drawImage(flames[Math.min(7,Math.floor(age*8))],x-size*.5,y-size*1.13,size,size*1.39);
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
    streak(lane) {
      streakLane = lane;
      streakStart = performance.now();
    },
    clearStreak() { streakStart = -Infinity; },
    setPower,
    draw,
    reset() {
      beatIndex = firstVisible = end = nextImpact = 0;
      lightUntil.fill(0);
      activeKeys.fill(0);
      for (const burst of impacts) burst.start = -10000;
      wakeMotion?.cancel();
      powerFlash = -Infinity;
      streakStart = -Infinity;
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
