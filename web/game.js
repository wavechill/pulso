import { mountSoloLife } from "./solo-life.js";
import { mountPowerHud } from "./power-hud.js";
import { createRail, noteFeedback } from "./rail.js";
import { mountBattleHud } from "./battle-hud.js";
import { mountInterval } from "./solo-interval.js";
import { mountGameSounds } from "./game-sounds.js";
import {
  $,
  esc,
  clock,
  icon,
  lanes,
  diffnames,
  prefs,
  save,
  api,
  wait,
} from "./common.js";

const COUNT_IN = 3;
const ENCORE_TEMPO = 1.15;
const PERFECT_WINDOW = .055;
const COMBO_MILESTONES = new Set([8, 16, 32, 64]);
const NOTE_LEAD = { easy: 2.3, medium: 1.85, hard: 1.55 };
// Keep a short empty beat after the countdown, then leave room for a note
// at 0:00 to approach from the top of the rail.
const START_RUNWAY = 0.22 + NOTE_LEAD.easy;

let context, cache, pending;
export async function unlock() {
  context ??= new AudioContext({ latencyHint: "interactive" });
  if (context.state !== "running") await context.resume();
}
async function loadAudio(song, { tempo = 1, signal, featured = false } = {}) {
  const url = "/api/songs/" + encodeURIComponent(song.id) + (featured ? "/performance" : "/audio");
  const response = await fetch(tempo === 1 ? url : url + "?tempo=" + tempo, { signal });
  if (!response.ok) throw Error("Não foi possível abrir o áudio.");
  const raw = await response.arrayBuffer();
  const decoded = await new OfflineAudioContext(featured ? 4 : 2, 1, 48000).decodeAudioData(raw);
  if (!featured) return decoded;
  if (decoded.numberOfChannels !== 4) throw Error("Não foi possível abrir a parte isolada do mapa.");
  const stem = new AudioBuffer({numberOfChannels:2, length:decoded.length, sampleRate:decoded.sampleRate});
  stem.copyToChannel(decoded.getChannelData(2),0);
  stem.copyToChannel(decoded.getChannelData(3),1);
  return stem;
}
export async function prepare(song, onStatus, {isolated=false, map:customMap}={}) {
  const map = customMap || await api("songs/" + song.id + "/chart");
  if (!Object.values(map.charts).some(rows => rows.length)) throw Error("Esta gravação tem poucos sons distinguíveis para montar uma partida. Você pode ouvi-la no Player.");
  if (isolated && !map.performance) throw Error("Esta faixa ainda não tem uma parte isolada.");
  const edition = isolated ? map.performance.id+"/"+map.performance.revision : "original";
  const key = song.id+"/"+edition;
  if (isolated) onStatus?.("Abrindo a parte isolada…");
  if (cache?.key===key) return {id:song.id,map,buffer:cache.buffer,isolated};
  const entry = pending?.key===key ? pending : {key,promise:loadAudio(song,{featured:isolated})};
  pending=entry;
  try {
    const buffer=await entry.promise;
    cache={key,buffer};
    return {id:song.id,map,buffer,isolated};
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
  practiceLoop = null,
  offerEncore = false,
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
    colors = ["#ff4d37", "#69ed42", "#ffd02d", "#23caff", "#bc65ff"];
  const originalNotes = track.map.charts[diff],
    flags = new Uint8Array(originalNotes.length),
    laneColors = lanes[diff].map((i) => colors[i]);
  let notes = originalNotes, buffer = track.buffer, duration = buffer.duration;
  let tempo = 1, firstRound = null, sectionStart = 0, sectionIndex = 0, sought = false;
  const lead = NOTE_LEAD[diff],
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
    resumePosition = -Infinity,
    inputSince = 0,
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
  const sections = track.map.performance?.sections || [];
  const sounds = mountGameSounds(context, options.effectsVolume);
  view.innerHTML =
    '<div class="game-layout ' +
    (room ? "cooperative" : "solo") +
    '"><div class="song-scenery" aria-hidden="true"></div><div class="split game-heading"><div><p class="eyebrow">' +
    (room ? "Cooperativo" : practice ? "Solo · Treino" : "Solo") +
    " · " +
    esc(track.map.difficultyLabel || diffnames[diff]) +
    (!room && offerEncore ? ' <span id="solo-round">· Rodada 1</span>' : "") +
    "</p><h2>" +
    esc(s.title) +
    '</h2><span id="playing-part" hidden></span></div><div class="actions">' +
    (!room
      ? '<button class="icon-btn" id="pause" title="Pausar (P)" aria-label="Pausar">' +
        icon("pause") +
        "</button>"
      : "") +
    (s.cover ? '<button class="icon-btn" id="scenery" aria-label="Capa da música como fundo">' + icon("scenery") + '</button>' : '') +
    '<button class="icon-btn" id="fullscreen" aria-label="Tela cheia" title="Tela cheia (F11 pelo navegador)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 9V3h6m6 0h6v6M3 15v6h6m6 0h6v-6"/></svg></button><button class="icon-btn" id="quit" aria-label="Sair da partida">' +
    icon("x") +
    "</button></div></div>" +
    (room ? '<div id="battle-hud"></div>' : "") +
    '<section class="play-area" aria-label="Seu trilho">' +
    (room ? "" : '<div id="solo-life"></div>') +
    '<div class="stage note-stage"><canvas class="rail-base" aria-hidden="true"></canvas><canvas id="note-layer" tabindex="0" aria-label="Trilho musical. Use as teclas ' +
    keys.join(", ") +
    '."></canvas><div class="rail-judgment" aria-hidden="true" hidden></div><div class="rail-streak" role="status" aria-atomic="true"></div><div id="round-countdown" aria-hidden="true" hidden></div></div><div id="power-hud"></div><div class="scorebar"><span><strong id="points">0</strong> pontos</span><span><strong id="combo">0</strong> sequência</span><span><strong id="hits">0</strong> acertos</span></div>' +
    (room ? '<progress id="timeline" aria-label="Progresso da faixa" max="' :
      '<input id="timeline" class="game-seek" type="range" min="0" step="any" aria-label="Posição da música" title="Voltar ou avançar e reiniciar a pontuação do trecho" max="') +
    duration +
    '" value="0">' + (room ? '</progress>' : '') +
    '<div class="split small game-position"><span id="elapsed">0:00</span><span id="game-duration">' +
    clock(duration) +
    "</span></div></section>" +
    (!room && offerEncore ? '<section class="solo-interval" hidden aria-label="Intervalo entre rodadas"></section>' : "") +
    "</div>";

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
  const judgment = view.querySelector(".rail-judgment");
  const streak = view.querySelector(".rail-streak");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let judgmentTimer, streakTimer, judgmentMotion, streakShake;
  function clearJudgment() {
    clearTimeout(judgmentTimer);
    clearTimeout(streakTimer);
    judgmentMotion?.cancel();
    streakShake?.cancel();
    rail.clearStreak();
    judgment.hidden = true;
    streak.textContent = "";
  }
  function applaud(delta, late, lane) {
    const precise = delta <= PERFECT_WINDOW, close = delta <= .105;
    judgment.dataset.grade = precise ? "perfect" : close ? "good" : "near";
    judgment.textContent = precise ? "Perfeito!" : close ? "Muito bem!" : late ? "Um pouco tarde" : "Um pouco cedo";
    judgment.hidden = false;
    clearTimeout(judgmentTimer);
    judgmentTimer = setTimeout(() => { judgment.hidden = true; }, 520);
    if (!reducedMotion) {
      judgmentMotion?.cancel();
      judgmentMotion = judgment.animate(
        [{ transform: "translateY(3px)", opacity: .7 }, { transform: "translateY(0)", opacity: 1 }],
        { duration: 110, easing: "ease-out" },
      );
    }
    if (COMBO_MILESTONES.has(combo) || (combo >= 100 && combo % 50 === 0)) {
      sounds.play("combo");
      clearTimeout(streakTimer);
      streak.textContent = combo + " em sequência!";
      if (!reducedMotion) {
        rail.streak();
        streakShake?.cancel();
        streakShake = rail.canvas.parentElement.animate([
          { transform: "translate(0, 0)" }, { transform: "translate(-4px, 3px)" },
          { transform: "translate(3px, -2px)" }, { transform: "translate(-1px, 1px)" },
          { transform: "translate(0, 0)" },
        ], { duration: 220, easing: "ease-out" });
      }
      streakTimer = setTimeout(() => { streak.textContent = ""; }, 1300);
    }
  }
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
      clearJudgment();
      lastLabel = "Soltou cedo";
      sounds.play("miss");
      rail.impact(lane, true);
    }
    feedbackUntil = performance.now() + 360;
    report();
  }
  const layout = view.querySelector(".game-layout"),
    fullButton = $("#fullscreen");
  const backdropButton = layout.querySelector("#scenery");
  const scenery = layout.querySelector(".song-scenery");
  if (s.cover) {
    const cover = new Image();
    cover.alt = "";
    cover.onerror = () => { cover.hidden = true; };
    cover.src = s.cover;
    scenery.append(cover);
  }
  function setBackdrop(choice) {
    const showing = choice === "art" && !!s.cover;
    layout.dataset.backdrop = showing ? "art" : "black";
    if (backdropButton) {
      backdropButton.setAttribute("aria-pressed", String(showing));
      backdropButton.title = showing ? "Usar fundo preto" : "Usar a capa da música";
    }
  }
  setBackdrop(options.gameBackdrop);
  if (backdropButton) backdropButton.onclick = () => {
    const choice = layout.dataset.backdrop === "art" ? "black" : "art";
    setBackdrop(choice);
    save({gameBackdrop: choice});
    rail.canvas.focus({preventScroll:true});
  };
  const partLabel = layout.querySelector("#playing-part");
  let displayedSection;
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
  function startAudio(time, resume = false) {
    nextSample = 0;
    const stamp = performance.now();
    const audible = deviceTime(stamp);
    const continuing = resume && time >= 0;
    // A room that started earlier must catch up. A paused solo starts at its
    // exact saved sample and waits for that sample to reach the output.
    const renderTime = context.currentTime;
    origin = (continuing ? renderTime : audible) - time;
    const when = Math.max(renderTime, origin);
    resumePosition = continuing ? time : -Infinity;
    inputSince = stamp + (continuing ? 1000 * Math.max(0, when - audible) : 0);
    source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(gain);
    source.start(when, Math.min(duration, Math.max(0, when - origin)));
  }
  function haltSource(when = context.currentTime) {
    if (source) {
      try {
        source.stop(when);
      } catch {}
      source.disconnect();
      source = null;
    }
  }

  function now(stamp = performance.now()) {
    return paused
      ? pausedTime
      : Math.max(resumePosition, deviceTime(stamp) - origin);
  }
  function resetSoloAttempt() {
    score = hits = perfect = combo = best = 0;
    next = sectionIndex;
    energy = powerTick = feedbackUntil = nextProgress = 0;
    powered = powerKey = false;
    lastLabel = "";
    clearJudgment();
    flags.fill(0);
    flags.fill(2, 0, sectionIndex);
    holds.fill(-1);
    pointers.clear();
    rail.reset();
    soloLife.reset();
    powerHud.update();
    report();
  }
  function restartSolo() {
    retryAt = 0;
    resetSoloAttempt();
    if (sectionStart > 0) startAudio(sectionStart, true);
    else startAudio(-(COUNT_IN + START_RUNWAY));
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
    // Keep the hardware event time even if a busy UI delivered it late.
    const eventStamp = inputTime(stamp);
    if (eventStamp < inputSince) return;
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
      clearJudgment();
      lastLabel = "Fora do tempo";
      sounds.play("miss");
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
    const precise = min <= PERFECT_WINDOW;
    if (precise) perfect++;
    applaud(min, t > n.t, lane);
    score +=
      (!room && powered ? 2 : 1) *
      (precise ? 100 : 70) *
      Math.min(4, 1 + Math.floor(combo / 12));
    lastLabel = n.duration ? "Segure " + keys[lane] : (room && noteFeedback(n)) || "";
    feedbackUntil = performance.now() + 320;
    if (room) links.push(bestIndex);
    report();
  }

  function inputTime(stamp) {
    const received = performance.now();
    return stamp > 0 && stamp <= received + 1 ? stamp : received;
  }

  function down(e) {
    if (modal?.open || done) return;
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
      if (!paused) finishHold(lane, now(inputTime(e.timeStamp)), true);
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
    if (!paused) finishHold(lane, now(inputTime(e.timeStamp)), true);
  }
  function pause() {
    if (room || ended || done) return;
    if (paused) {
      if (!retryAt) startAudio(pausedTime, true);
      paused = false;
    } else {
      power(false);
      // Anything still buffered by the device at this click belongs after
      // the pause. Replay it on resume so no unplayable notes are skipped.
      if (!retryAt) pausedTime = Math.min(duration, now());
      for (let l = 0; l < holds.length; l++) finishHold(l, pausedTime, true);
      paused = true;
      clearJudgment();
      haltSource();
      rail.activeKeys.fill(0);
    }
    updatePauseButton();
  }
  function updatePauseButton() {
    $("#pause").innerHTML = icon(paused ? "play" : "pause");
    $("#pause").setAttribute("aria-label", paused ? "Continuar" : "Pausar");
  }
  function seek() {
    if (room || ended || done) return;
    if (!paused) {
      power(false);
      haltSource();
      paused = true;
    }
    retryAt = 0;
    sought = true;
    // Select heads only. A sustain whose head precedes the seek point must
    // never score, render as held or incur a life penalty.
    const at = Math.max(0, Math.min(duration, timeline.valueAsNumber));
    if (practiceLoop) {
      practiceLoop = null;
      $("#solo-round").textContent = "· Rodada 1";
    }
    setSection(at);
    pausedTime = at;
    resetSoloAttempt();
    rail.activeKeys.fill(0);
    countdown.hidden = true;
    lastCount = 0;
    updatePauseButton();
  }
  function setSection(at) {
    let left = 0, right = notes.length;
    while (left < right) {
      const middle = (left + right) >>> 1;
      if (notes[middle].t < at) left = middle + 1;
      else right = middle;
    }
    sectionStart = at;
    sectionIndex = left;
  }
  function startPracticeLoop() {
    haltSource();
    setSection(practiceLoop.from);
    sought = true;
    resetSoloAttempt();
    $("#solo-round").textContent = "· Riff " + clock(practiceLoop.from) + "–" + clock(practiceLoop.to);
    startAudio(Math.max(0, practiceLoop.from - lead), true);
  }
  function blur() {
    power(false);
    for (let l = 0; l < holds.length; l++) finishHold(l, now(), true);
    rail.activeKeys.fill(0);
    if (!room && !paused && !done && !ended) pause();
  }
  $("#pause")?.addEventListener("click", pause);
  if (!room) timeline.addEventListener("input", seek);
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
    clearJudgment();
    intervalPanel?.close();
    haltSource();
    gain.disconnect();
    sounds.destroy();
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
      score: teamSnapshot ? teamSnapshot.players[teamSnapshot.me].score : score + (firstRound?.score || 0),
      hits: hits + (firstRound?.hits || 0),
      perfect: perfect + (firstRound?.perfect || 0),
      best: Math.max(best, firstRound?.best || 0),
      rounds: firstRound ? 2 : 1,
      practice,
      sought,
      total: (firstRound ? notes.length : 0) + notes.length - sectionIndex,
      team: teamSnapshot,
    });
  }
  const intervalPanel = !room && offerEncore ? mountInterval(view.querySelector(".solo-interval"), {
    async next(signal) {
      await unlock();
      const faster = await loadAudio(s, { tempo: ENCORE_TEMPO, signal, featured: track.isolated });
      if (ended || signal.aborted) return;
      if (Math.abs(faster.duration - track.buffer.duration / ENCORE_TEMPO) > .15)
        throw Error("A duração da faixa acelerada não corresponde ao mapa.");
      firstRound = { score, hits, perfect, best };
      tempo = ENCORE_TEMPO;
      buffer = faster;
      duration = buffer.duration;
      // The tempo-adjusted buffer keeps its original pitch. All judgment times
      // remain seconds at the output device, including the 55 ms perfect window.
      notes = originalNotes.map(n => n.duration
        ? { ...n, t: n.t / tempo, duration: n.duration / tempo }
        : { ...n, t: n.t / tempo });
      timeline.max = duration;
      timeline.value = 0;
      lastSecond = -1;
      timeNode.textContent = clock(0);
      $("#game-duration").textContent = clock(duration);
      $("#solo-round").textContent = "· Rodada 2 · 1,15×";
      view.querySelector(".play-area").hidden = false;
      $("#pause").hidden = false;
      intervalPanel.close();
      done = false;
      restartSolo();
      lastFrameStamp = 0;
      raf = requestAnimationFrame(frame);
      rail.canvas.focus({ preventScroll: true });
    },
    finish: () => finish(),
  }) : null;
  async function audioEnd() {
    if (done) return;
    done = true;
    if (!room) {
      if (intervalPanel && !firstRound && !sought) {
        haltSource();
        clearJudgment();
        view.querySelector(".play-area").hidden = true;
        $("#pause").hidden = true;
        intervalPanel.show({ score, perfect, best });
      } else finish();
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

  let lastFrameStamp = 0,
    lastInterval = 0,
    refreshInterval = 0;
  function frame(stamp) {
    if (ended) return;
    const interval = stamp - lastFrameStamp;
    const lateFrame =
      lastFrameStamp > 0 && interval > Math.max(50, refreshInterval * 3);
    // Two agreeing intervals distinguish a refresh-rate change from one
    // missed frame. The game still samples and draws once per actual refresh.
    if (
      interval >= 0.6 &&
      interval <= 50 &&
      Math.abs(interval - lastInterval) < Math.max(0.25, interval * 0.12)
    )
      refreshInterval = (interval + lastInterval) / 2;
    lastInterval = interval;
    lastFrameStamp = stamp;
    if (retryAt) {
      if (paused || stamp < retryAt) {
        rail.draw(
          pausedTime * tempo,
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
    const frameNow = performance.now();
    if (practiceLoop && !paused && now(frameNow) >= practiceLoop.to) startPracticeLoop();
    const t = now(frameNow);
    // The canvas being painted will be presented at the next refresh.
    // Input and scoring continue to use the actual event/output time.
    const visualTime = now(Math.max(frameNow, stamp + refreshInterval));
    // On a missed run of frames, queued OS input may be dispatched just
    // after this callback. Let it be judged before finalizing those misses.
    const judge = lateFrame ? t - interval / 1000 : t;
    if (!paused && !done) {
      for (let lane = 0; lane < holds.length; lane++)
        if (holds[lane] >= 0) finishHold(lane, judge);
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
      (flags[next] || (!paused && notes[next].t < judge - windowHit))
    ) {
      if (!flags[next]) {
        flags[next] = 2;
        clearJudgment();
        rail.impact(notes[next].lane, true);
        sounds.play("miss");
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
      visualTime * tempo,
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
      if (sections.length) {
        const songTime = Math.max(0,t*tempo);
        const current = sections.find(section => section.start <= songTime && songTime < section.end);
        if (current !== displayedSection) {
          displayedSection = current;
          partLabel.hidden = !current;
          partLabel.textContent = current ? "Você toca · " + current.label : "";
        }
      }
      const bounded = Math.max(0, Math.min(duration, t)),
        second = Math.floor(bounded);
      timeline.value = bounded;
      if (!room) timeline.style.setProperty("--timeline-progress", 100 * bounded / duration + "%");
      if (second !== lastSecond) {
        timeNode.textContent = clock(second);
        if (!room) timeline.setAttribute("aria-valuetext", clock(second));
        lastSecond = second;
      }
      nextProgress = stamp + 100;
    }
    if (!retryAt && t >= duration + 0.2) audioEnd();
    if (!ended && !(done && intervalPanel)) raf = requestAnimationFrame(frame);
  }
  if (practice && practiceLoop && practiceLoop.to <= duration) startPracticeLoop();
  else {
    practiceLoop = null;
    startAudio(-(typeof delay === "function" ? delay() : delay));
  }
  if (room) updateTeam(room);
  raf = requestAnimationFrame(frame);
  return { stop, updateTeam, teamResult: finish };
}
