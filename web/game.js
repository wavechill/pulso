import { mountSoloLife } from "./solo-life.js";
import { mountPowerHud } from "./power-hud.js";
import { createRail, noteFeedback } from "./rail.js";
import { mountBattleHud } from "./battle-hud.js";
import {
  $,
  esc,
  clock,
  icon,
  lanes,
  diffnames,
  prefs,
  api,
  wait,
} from "./common.js";

const COUNT_IN = 3;
const NOTE_LEAD = { easy: 2.3, medium: 1.85, hard: 1.55 };
// Keep a short empty beat after the countdown, then leave room for a note
// at 0:00 to approach from the top of the rail.
const START_RUNWAY = 0.22 + NOTE_LEAD.easy;

let context, cache, pending;
export async function unlock() {
  context ??= new AudioContext({ latencyHint: "interactive" });
  if (context.state !== "running") await context.resume();
}
export async function prepare(song) {
  if (pending?.id === song.id) return pending.promise;
  const promise = Promise.all([
    api("songs/" + song.id + "/chart"),
    cache?.id === song.id
      ? cache.buffer
      : fetch("/api/songs/" + song.id + "/audio")
          .then((r) => {
            if (!r.ok) throw Error("Não foi possível abrir o áudio.");
            return r.arrayBuffer();
          })
          .then((raw) =>
            new OfflineAudioContext(2, 1, 48000).decodeAudioData(raw),
          ),
  ]).then(([map, buffer]) => (cache = { id: song.id, map, buffer }));
  const entry = { id: song.id, promise };
  pending = entry;
  try {
    return await promise;
  } finally {
    if (pending === entry) pending = null;
  }
}
export function startGame({
  view,
  s,
  track,
  diff,
  practice = false,
  delay = COUNT_IN + START_RUNWAY,
  room,
  onHits,
  onFinish,
  onPower,
  onEnd,
  onExit,
}) {
  const options = prefs(),
    keys = lanes[diff].map((i) => options.keys[i]),
    colors = ["#f88d69", "#aac4a0", "#e7c879", "#89b9cc", "#be9bca"];
  const notes = track.map.charts[diff],
    flags = new Uint8Array(notes.length),
    laneColors = lanes[diff].map((i) => colors[i]);
  const duration = track.buffer.duration,
    lead = NOTE_LEAD[diff],
    windowHit = 0.145;
  let score = 0,
    hits = 0,
    perfect = 0,
    combo = 0,
    best = 0,
    next = 0,
    paused = false,
    pausedTime = 0,
    retryAt = 0,
    ended = false,
    done = false,
    raf,
    source,
    origin,
    lastLabel = "",
    feedbackUntil = 0,
    links = [],
    completedHolds = [],
    sending = false,
    team = room,
    lastScore = -1,
    lastCombo = -1,
    lastHits = -1;
  const gain = context.createGain();
  gain.gain.value = options.volume;
  gain.connect(context.destination);
  view.innerHTML =
    '<div class="game-layout ' +
    (room ? "cooperative" : "solo") +
    '"><div class="split game-heading"><div><p class="eyebrow">' +
    (room ? "Cooperativo" : practice ? "Solo · Treino" : "Solo") +
    " · " +
    diffnames[diff] +
    "</p><h2>" +
    esc(s.title) +
    '</h2></div><div class="actions">' +
    (!room
      ? '<button class="icon-btn" id="pause" title="Pausar (P)" aria-label="Pausar">' +
        icon("pause") +
        "</button>"
      : "") +
    '<button class="icon-btn" id="fullscreen" aria-label="Tela cheia" title="Tela cheia (F11 pelo navegador)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 9V3h6m6 0h6v6M3 15v6h6m6 0h6v-6"/></svg></button><button class="icon-btn" id="quit" aria-label="Sair da partida">' +
    icon("x") +
    "</button></div></div>" +
    (room ? '<div id="battle-hud"></div>' : "") +
    '<section class="play-area" aria-label="Seu trilho">' +
    (room ? "" : '<div id="solo-life"></div>') +
    '<div class="stage note-stage"><canvas class="rail-base" aria-hidden="true"></canvas><canvas id="note-layer" tabindex="0" aria-label="Trilho musical. Use as teclas ' +
    keys.join(", ") +
    '."></canvas><div id="round-countdown" aria-hidden="true" hidden></div></div><div id="power-hud"></div><div class="scorebar"><span><strong id="points">0</strong> pontos</span><span><strong id="combo">0</strong> sequência</span><span><strong id="hits">0</strong> acertos</span></div><progress id="timeline" max="' +
    duration +
    '" value="0"></progress><div class="split small game-position"><span id="elapsed">0:00</span><span>' +
    clock(duration) +
    "</span></div></section></div>";

  const rail = createRail({
    stage: view.querySelector(".note-stage"),
    notes,
    keys,
    colors: laneColors,
    lead,
    coop: Boolean(room),
    beats: track.map.beats || [],
  });
  const battleHud = room
    ? mountBattleHud(view.querySelector("#battle-hud"), room)
    : null;
  const soloLife = room
    ? null
    : mountSoloLife($("#solo-life"), {
        total: notes.length,
        duration,
        practice,
      });
  const holds = new Int32Array(keys.length).fill(-1);
  const pointers = new Map();
  let energy = 0,
    powered = false,
    powerKey = false,
    powerTick = 0,
    powerRequest = Promise.resolve();
  const chargePerHit = Math.min(
    8,
    Math.max(2, (9 * duration) / Math.max(1, notes.length)),
  );
  const powerHud = mountPowerHud($("#power-hud"), {
    solo: !room,
    onPress: () => power(true),
    onRelease: () => power(false),
  });
  function power(pressed) {
    if (pressed === powerKey || ended) return;
    powerKey = pressed;
    if (!pressed) rail.setPower(false, energy);
    if (pressed && (paused || retryAt || done || now() < 0 || modal?.open))
      return;
    if (pressed && energy < 20) {
      lastLabel = "Carregue 20% de energia";
      feedbackUntil = performance.now() + 1000;
      return;
    }
    if (room) {
      // Serialize press/release so a rapid tap cannot leave power latched over LAN.
      powerRequest = powerRequest
        .then(() => onPower(pressed))
        .then(updateTeam)
        .catch(() => {});
    } else {
      powered = pressed && energy >= 20;
      powerTick = now();
      powerHud.update({ energy, active: powered });
      rail.setPower(powered, energy);
    }
  }
  function finishHold(lane, at, release = false) {
    const i = holds[lane];
    if (i < 0) return;
    const n = notes[i];
    const complete = at >= n.t + n.duration - (release ? 0.075 : 0);
    if (!release && !complete) return;
    flags[i] = 1;
    holds[lane] = -1;
    if (complete) {
      score += 50 * (!room && powered ? 2 : 1);
      if (room) completedHolds.push(i);
      lastLabel = "Sustentada";
      rail.impact(lane);
    } else {
      combo = 0;
      lastLabel = "Soltou cedo";
      rail.impact(lane, true);
    }
    feedbackUntil = performance.now() + 360;
    report();
  }
  const layout = view.querySelector(".game-layout"),
    fullButton = $("#fullscreen");
  const desktop = window.pulsoDesktop ? window.chrome.webview : null;
  const fullChanged = () => {
    const enabled = desktop
      ? layout.classList.contains("native-fullscreen")
      : document.fullscreenElement === layout;
    fullButton.setAttribute(
      "aria-label",
      enabled ? "Sair de tela cheia" : "Tela cheia",
    );
    fullButton.setAttribute("aria-pressed", String(enabled));
  };
  function nativeFullscreen(event) {
    if (event.data !== "fullscreen:on" && event.data !== "fullscreen:off")
      return;
    layout.classList.toggle(
      "native-fullscreen",
      event.data === "fullscreen:on",
    );
    fullChanged();
  }
  fullButton.onclick = async () => {
    if (desktop) return desktop.postMessage("fullscreen");
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await layout.requestFullscreen();
    } catch {
      lastLabel = "Use F11 para tela cheia";
      feedbackUntil = performance.now() + 2200;
    }
  };
  desktop?.addEventListener("message", nativeFullscreen);
  document.addEventListener("fullscreenchange", fullChanged);
  const scoreNode = $("#points"),
    comboNode = $("#combo"),
    hitNode = $("#hits");
  const timeNode = $("#elapsed"),
    timeline = $("#timeline"),
    modal = $("#modal"),
    countdown = $("#round-countdown");
  let lastSecond = -1,
    nextProgress = 0,
    lastCount = 0;
  const keyMap = new Map(keys.map((key, i) => ["Key" + key, i]));
  const formatter = new Intl.NumberFormat("pt-BR");

  // AudioContext.currentTime advances in audio blocks. Align the performance
  // clock with the output device, then interpolate between samples for paint
  // and input timestamps, including on displays faster than the audio clock.
  let audioAnchor = 0,
    perfAnchor = -Infinity,
    nextSample = 0;
  function deviceTime(stamp) {
    if (stamp >= nextSample || !Number.isFinite(perfAnchor)) {
      const time = context.getOutputTimestamp?.();
      const currentStamp = performance.now();
      const valid =
        time &&
        time.performanceTime > 0 &&
        currentStamp - time.performanceTime < 300;
      const observed = valid
        ? time.contextTime + (currentStamp - time.performanceTime) / 1000
        : context.currentTime -
          (context.outputLatency || 0) -
          (context.baseLatency || 0);
      const predicted = audioAnchor + (currentStamp - perfAnchor) / 1000;
      const drift = observed - predicted;
      // Smooth small device timestamp fluctuations; reacquire after sleep or
      // an output-device change without carrying an obsolete timing offset.
      audioAnchor =
        Number.isFinite(drift) && Math.abs(drift) < 0.06
          ? predicted + drift * 0.2
          : observed;
      perfAnchor = currentStamp;
      nextSample = currentStamp + 100;
    }
    return audioAnchor + (stamp - perfAnchor) / 1000;
  }
  function startAudio(time) {
    nextSample = 0;
    const audible = deviceTime(performance.now());
    origin = audible - time;
    const when = Math.max(context.currentTime, origin);
    source = context.createBufferSource();
    source.buffer = track.buffer;
    source.connect(gain);
    source.start(when, Math.min(duration, Math.max(0, when - origin)));
  }
  function haltSource() {
    if (source) {
      try {
        source.stop();
      } catch {}
      source.disconnect();
      source = null;
    }
  }

  function now(stamp = performance.now()) {
    return paused ? pausedTime : deviceTime(stamp) - origin;
  }
  function restartSolo() {
    retryAt = 0;
    score = hits = perfect = combo = best = next = 0;
    energy = powerTick = feedbackUntil = nextProgress = 0;
    powered = powerKey = false;
    lastLabel = "";
    flags.fill(0);
    holds.fill(-1);
    pointers.clear();
    rail.reset();
    soloLife.reset();
    powerHud.update();
    report();
    startAudio(-(COUNT_IN + START_RUNWAY));
  }
  function report() {
    const points = team ? team.players[team.me].score : score;
    if (points !== lastScore) {
      scoreNode.textContent = formatter.format(points);
      lastScore = points;
    }
    if (combo !== lastCombo) {
      comboNode.textContent = combo;
      lastCombo = combo;
    }
    if (hits !== lastHits) {
      hitNode.textContent = hits;
      lastHits = hits;
    }
  }
  function mark(lane, stamp) {
    if (paused || retryAt || ended || done || modal?.open) return;
    // Event timestamps survive a delayed keyboard dispatch on a busy frame.
    const eventStamp =
      Math.abs(performance.now() - stamp) < 250 ? stamp : performance.now();
    const t = now(eventStamp);
    if (t < -0.18) return;
    rail.illuminate(lane, eventStamp);
    let bestIndex = -1,
      min = windowHit;
    for (let i = next; i < notes.length && notes[i].t <= t + windowHit; i++) {
      if (flags[i] || notes[i].lane !== lane) continue;
      const delta = Math.abs(notes[i].t - t);
      if (delta <= min) {
        min = delta;
        bestIndex = i;
      }
    }
    if (bestIndex < 0) {
      combo = 0;
      lastLabel = "Fora do tempo";
      feedbackUntil = performance.now() + 230;
      report();
      return;
    }
    const n = notes[bestIndex];
    flags[bestIndex] = n.duration ? 3 : 1;
    if (n.duration) holds[lane] = bestIndex;
    rail.impact(lane);
    soloLife?.hit();
    if (!room && !powered) {
      energy = Math.min(100, energy + chargePerHit);
      powerHud.update({ energy, active: powered });
    }
    hits++;
    combo++;
    best = Math.max(best, combo);
    const precise = min <= 0.055;
    if (precise) perfect++;
    score +=
      (!room && powered ? 2 : 1) *
      (precise ? 100 : 70) *
      Math.min(4, 1 + Math.floor(combo / 12));
    lastLabel =
      (n.duration ? "Segure " + keys[lane] : "") ||
      (room && noteFeedback(n)) ||
      (precise
        ? "Perfeito"
        : t > notes[bestIndex].t
          ? "Um pouco tarde"
          : "Um pouco cedo");
    feedbackUntil = performance.now() + 320;
    if (room) links.push(bestIndex);
    report();
  }

  function down(e) {
    if (modal?.open) return;
    if (
      desktop &&
      e.code === "Escape" &&
      layout.classList.contains("native-fullscreen")
    ) {
      desktop.postMessage("exit-fullscreen");
      return;
    }
    if (e.code === "Space") {
      e.preventDefault();
      if (!e.repeat) power(true);
      return;
    }
    if (e.code === "KeyP" && !keyMap.has(e.code) && !room) {
      e.preventDefault();
      if (!e.repeat) pause();
      return;
    }
    const lane = keyMap.get(e.code);
    if (lane === undefined) return;
    e.preventDefault();
    if (!rail.activeKeys[lane] && !e.repeat) {
      rail.activeKeys[lane] = 1;
      mark(lane, e.timeStamp);
    }
  }
  function up(e) {
    if (e.code === "Space") {
      power(false);
      return;
    }
    const lane = keyMap.get(e.code);
    if (lane !== undefined) {
      rail.activeKeys[lane] = 0;
      if (!paused) finishHold(lane, now(e.timeStamp), true);
    }
  }
  function pressTouch(e) {
    const lane = rail.laneAt(e.clientX);
    if (lane >= 0) {
      pointers.set(e.pointerId, lane);
      rail.canvas.setPointerCapture(e.pointerId);
      rail.activeKeys[lane] = 1;
      mark(lane, e.timeStamp);
    }
  }
  function releaseTouch(e) {
    const lane = pointers.get(e.pointerId);
    if (lane === undefined) return;
    pointers.delete(e.pointerId);
    rail.activeKeys[lane] = 0;
    finishHold(lane, now(e.timeStamp), true);
  }
  function pause() {
    if (room || ended || done) return;
    if (paused) {
      if (!retryAt) startAudio(pausedTime);
      paused = false;
    } else {
      power(false);
      if (!retryAt) pausedTime = now();
      for (let l = 0; l < holds.length; l++) finishHold(l, pausedTime, true);
      paused = true;
      haltSource();
      rail.activeKeys.fill(0);
    }
    $("#pause").innerHTML = icon(paused ? "play" : "pause");
    $("#pause").setAttribute("aria-label", paused ? "Continuar" : "Pausar");
  }
  function blur() {
    power(false);
    for (let l = 0; l < holds.length; l++) finishHold(l, now(), true);
    rail.activeKeys.fill(0);
    if (!room && !paused && !done && !ended) pause();
  }
  $("#pause")?.addEventListener("click", pause);
  $("#quit").onclick = onExit;
  window.addEventListener("keydown", down);
  window.addEventListener("keyup", up);
  window.addEventListener("blur", blur);
  rail.canvas.addEventListener("pointerdown", pressTouch);
  rail.canvas.addEventListener("pointerup", releaseTouch);
  rail.canvas.addEventListener("pointercancel", releaseTouch);
  async function flush() {
    if (sending || (!links.length && !completedHolds.length) || ended) return;
    sending = true;
    const batch = links.splice(0, 120),
      sustained = completedHolds.splice(0, 120);
    try {
      const snap = await onHits(batch, sustained);
      if (snap?.boss) updateTeam(snap);
    } catch {
      links.unshift(...batch);
      completedHolds.unshift(...sustained);
    } finally {
      sending = false;
    }
  }
  const timer = room ? setInterval(flush, 110) : null;
  function updateTeam(value) {
    if (!value || ended || value.serverTime < (team?.serverTime || 0)) return;
    team = value;
    battleHud?.update(value);
    const player = value.players[value.me];
    energy = player.energy || 0;
    powered = !!player.powerActive;
    powerHud.update({ energy, active: powered });
    report();
  }
  function stop() {
    if (ended) return;
    ended = true;
    haltSource();
    gain.disconnect();
    cancelAnimationFrame(raf);
    clearInterval(timer);
    rail.destroy();
    battleHud?.destroy();
    soloLife?.destroy();
    powerHud.destroy();
    document.removeEventListener("fullscreenchange", fullChanged);
    desktop?.removeEventListener("message", nativeFullscreen);
    if (desktop && layout.classList.contains("native-fullscreen"))
      desktop.postMessage("exit-fullscreen");
    if (document.fullscreenElement === layout)
      document.exitFullscreen().catch(() => {});
    rail.canvas.removeEventListener("pointerup", releaseTouch);
    rail.canvas.removeEventListener("pointercancel", releaseTouch);
    window.removeEventListener("keydown", down);
    window.removeEventListener("keyup", up);
    window.removeEventListener("blur", blur);
    rail.canvas.removeEventListener("pointerdown", pressTouch);
  }
  function finish(teamSnapshot = null) {
    if (ended) return;
    stop();
    onEnd({
      s,
      score: teamSnapshot ? teamSnapshot.players[teamSnapshot.me].score : score,
      hits,
      perfect,
      best,
      practice,
      total: notes.length,
      team: teamSnapshot,
    });
  }
  async function audioEnd() {
    if (done) return;
    done = true;
    if (!room) {
      finish();
      return;
    }
    lastLabel = "Aguardando a equipe";
    for (
      let n = 0;
      n < 18 && !ended && (sending || links.length || completedHolds.length);
      n++
    ) {
      await flush();
      await wait(120);
    }
    if (ended) return;
    try {
      const snap = await onFinish();
      if (snap.state === "finished") finish(snap);
    } catch {
      /* O servidor também encerra pelo relógio da sala. */
    }
  }

  function frame(stamp) {
    if (ended) return;
    if (retryAt) {
      if (paused || stamp < retryAt) {
        rail.draw(
          pausedTime,
          stamp,
          next,
          flags,
          "",
          paused ? "Pausado" : "Vida zerada · reiniciando",
        );
        raf = requestAnimationFrame(frame);
        return;
      }
      restartSolo();
    }
    const t = now(performance.now()),
      judge = t;
    if (!paused && !done) {
      for (let lane = 0; lane < holds.length; lane++)
        if (holds[lane] >= 0) finishHold(lane, t);
      if (!room && powered) {
        energy = Math.max(0, energy - Math.max(0, t - powerTick) * 22);
        powerTick = t;
        if (!energy) powered = false;
        powerHud.update({ energy, active: powered });
      }
    }
    let missed = false;
    while (
      next < notes.length &&
      (flags[next] || notes[next].t < judge - windowHit)
    ) {
      if (!flags[next]) {
        flags[next] = 2;
        rail.impact(notes[next].lane, true);
        soloLife?.miss();
        lastLabel = "Nota perdida";
        feedbackUntil = performance.now() + 340;
        combo = 0;
        missed = true;
      }
      next++;
    }
    if (missed) report();
    if (soloLife && !practice && soloLife.health() === 0) {
      retryAt = stamp + 950;
      pausedTime = t;
      power(false);
      haltSource();
      rail.activeKeys.fill(0);
    }
    const count =
      paused || done
        ? 0
        : Math.min(COUNT_IN, Math.max(0, Math.ceil(-t - START_RUNWAY)));
    if (count !== lastCount) {
      countdown.hidden = !count;
      if (count) countdown.textContent = String(count);
      lastCount = count;
    }
    rail.setPower(powered && powerKey && !paused && !done && !retryAt, energy);
    rail.draw(
      t,
      stamp,
      next,
      flags,
      stamp < feedbackUntil ? lastLabel : "",
      retryAt
        ? "Vida zerada · reiniciando"
        : paused
          ? "Pausado"
          : done
            ? "Aguardando a equipe"
            : "",
    );
    if (stamp >= nextProgress) {
      const bounded = Math.max(0, Math.min(duration, t)),
        second = Math.floor(bounded);
      timeline.value = bounded;
      if (second !== lastSecond) {
        timeNode.textContent = clock(second);
        lastSecond = second;
      }
      nextProgress = stamp + 100;
    }
    if (!retryAt && t >= duration + 0.2) audioEnd();
    if (!ended) raf = requestAnimationFrame(frame);
  }
  startAudio(-(typeof delay === "function" ? delay() : delay));
  if (room) updateTeam(room);
  raf = requestAnimationFrame(frame);
  return { stop, updateTeam, teamResult: finish };
}
