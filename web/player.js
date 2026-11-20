import { clock, esc, icon, prefs, save } from "./common.js";

function shuffle(ids) {
  for (let i = ids.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [ids[i], ids[j]] = [ids[j], ids[i]];
  }
  return ids;
}

function control(action, glyph, label) {
  return `<button type="button" class="mp-control" data-mp="${action}" aria-label="${label}" title="${label}">${icon(glyph)}</button>`;
}

export function createMusicPlayer({
  dock,
  cover,
  beforePlay,
  onOpen,
  onGame,
  onImport,
  onVisibility,
  onDelete,
  notify,
}) {
  const audio = new Audio();
  audio.preload = "metadata";
  const settings = prefs();
  audio.volume = Math.min(
    1,
    Math.max(0, Number(settings.playerVolume ?? settings.volume) || 0),
  );
  dock.innerHTML = `<div class="mp-dock-inner">
    <button type="button" class="mp-dock-song" data-mp="open" aria-label="Abrir Player"></button>
    <div class="mp-transport">
      ${control("shuffle", "shuffle", "Ativar aleatório")}
      ${control("previous", "previous", "Faixa anterior")}
      ${control("toggle", "play", "Reproduzir")}
      ${control("next", "next", "Próxima faixa")}
      ${control("repeat", "repeat", "Repetição desligada")}
    </div>
    <div class="mp-seek"><span data-mp-elapsed>0:00</span><input type="range" min="0" max="0" step="0.1" aria-label="Posição na faixa" disabled><span data-mp-duration>0:00</span></div>
    <div class="mp-volume">${control("mute", "volume", "Silenciar Player")}<input type="range" min="0" max="100" aria-label="Volume do Player"></div>
  </div>`;

  const dockSong = dock.querySelector(".mp-dock-song");
  const playButton = dock.querySelector('[data-mp="toggle"]');
  const seek = dock.querySelector(".mp-seek input");
  const elapsed = dock.querySelector("[data-mp-elapsed]");
  const duration = dock.querySelector("[data-mp-duration]");
  const volume = dock.querySelector(".mp-volume input");
  const mute = dock.querySelector('[data-mp="mute"]');
  let catalog = new Map();
  let hiddenSongs = new Map();
  let viewingHidden = false;
  let currentId = null;
  let loadedId = null;
  let currentPage = null;
  let playingGame = false;
  let upcoming = [];
  let manualQueue = [];
  let history = [];
  let shuffled = false;
  let repeat = 0;
  let playAttempt = 0;
  let playbackError = false;
  let search = "";

  const current = () => catalog.get(currentId);
  const isPlaying = () =>
    loadedId === currentId && !audio.paused && !audio.ended;
  const queue = () => [...manualQueue, ...upcoming];
  const normalize = (text) =>
    text
      .normalize("NFD")
      .replace(/\p{Diacritic}/gu, "")
      .toLowerCase();

  function setControl(button, glyph, label) {
    if (!button || button.getAttribute("aria-label") === label) return;
    button.setAttribute("aria-label", label);
    button.title = label;
    button.innerHTML = icon(glyph);
  }

  function reflectPosition() {
    const length =
      Number.isFinite(audio.duration) && loadedId === currentId
        ? audio.duration
        : Number(current()?.duration) || 0;
    seek.disabled =
      !loadedId || !Number.isFinite(audio.duration) || playbackError;
    seek.max = length;
    seek.value = Math.min(
      length,
      loadedId === currentId ? audio.currentTime : 0,
    );
    elapsed.textContent = clock(Number(seek.value));
    duration.textContent = clock(length);
    seek.setAttribute(
      "aria-valuetext",
      `${elapsed.textContent} de ${duration.textContent}`,
    );
  }

  function reflectVolume() {
    volume.value = audio.muted ? 0 : Math.round(audio.volume * 100);
    const quiet = audio.muted || audio.volume === 0;
    setControl(
      mute,
      quiet ? "mute" : "volume",
      quiet ? "Ativar som do Player" : "Silenciar Player",
    );
    mute.setAttribute("aria-pressed", String(quiet));
  }

  function reflectTransport() {
    const s = current();
    dock.hidden = !s || playingGame || (!currentPage && !loadedId);
    setControl(
      playButton,
      isPlaying() ? "pause" : "play",
      isPlaying() ? "Pausar" : "Reproduzir",
    );
    const random = dock.querySelector('[data-mp="shuffle"]');
    setControl(
      random,
      "shuffle",
      shuffled ? "Desativar aleatório" : "Ativar aleatório",
    );
    random.setAttribute("aria-pressed", String(shuffled));
    const loop = dock.querySelector('[data-mp="repeat"]');
    setControl(
      loop,
      repeat === 2 ? "repeatOne" : "repeat",
      ["Repetição desligada", "Repetir fila", "Repetir faixa"][repeat],
    );
    loop.setAttribute("aria-pressed", String(repeat > 0));
    dock.querySelector('[data-mp="previous"]').disabled =
      !loadedId && !history.length;
    dock.querySelector('[data-mp="next"]').disabled =
      !queue().length && repeat !== 1;
    const heroPlay = currentPage?.querySelector('[data-mp="toggle"]');
    if (heroPlay) {
      heroPlay.innerHTML = `${icon(isPlaying() ? "pause" : "play")}${isPlaying() ? "Pausar" : "Ouvir faixa"}`;
      currentPage.querySelector("[data-mp-status]").textContent = isPlaying()
        ? "Tocando agora"
        : loadedId === currentId
          ? "Reprodução pausada"
          : "Faixa selecionada";
      for (const row of currentPage.querySelectorAll("[data-mp-track]")) {
        const active = row.dataset.mpTrack === currentId;
        if (active) row.setAttribute("aria-current", "true");
        else row.removeAttribute("aria-current");
        row.querySelector(".mp-track-glyph").innerHTML = icon(
          active && isPlaying() ? "pause" : "play",
        );
      }
    }
  }

  function renderSong() {
    const s = current();
    dockSong.innerHTML = s
      ? `<img src="${cover(s)}" alt=""><span><strong>${esc(s.title)}</strong><small>${esc(s.artist)}</small></span>`
      : "";
    const hero = currentPage?.querySelector(".mp-hero-slot");
    if (hero)
      hero.innerHTML = s
        ? `<section class="mp-hero" aria-label="Faixa selecionada">
        <img class="mp-art" src="${cover(s)}" alt="">
        <div class="mp-hero-copy">
          <div class="mp-status">${icon("disc")}<span data-mp-status></span></div>
          <h2>${esc(s.title)}</h2>
          <p>${esc(s.artist)} <span>· ${clock(s.duration)}</span></p>
          <div class="mp-actions">
            <button type="button" class="btn primary" data-mp="toggle"></button>
            <button type="button" class="btn secondary" data-mp="game">${icon("pulse")}Jogar esta música</button>
          </div>
        </div>
      </section>`
        : `<div class="mp-empty mp-hero">${hiddenSongs.size ? "As faixas estão ocultas no Player." : "Adicione uma música para começar."}</div>`;
    reflectTransport();
    reflectPosition();
  }

  function renderTracks() {
    if (!currentPage) return;
    const query = normalize(search.trim());
    const songs = [...(viewingHidden ? hiddenSongs : catalog).values()].filter(
      (s) => normalize(`${s.title} ${s.artist}`).includes(query),
    );
    const hiddenToggle = currentPage.querySelector('[data-mp="hidden"]');
    hiddenToggle.hidden = !hiddenSongs.size && !viewingHidden;
    hiddenToggle.setAttribute("aria-pressed", String(viewingHidden));
    hiddenToggle.innerHTML = `${icon(viewingHidden ? "music" : "eyeOff")}${viewingHidden ? "Todas as visíveis" : `Ocultas (${hiddenSongs.size})`}`;
    currentPage.querySelector(".mp-list-heading h2").textContent = viewingHidden
      ? "Músicas ocultas"
      : "Músicas";
    currentPage.querySelector("[data-mp-count]").textContent =
      `${songs.length} ${songs.length === 1 ? "música" : "músicas"}`;
    currentPage.querySelector(".mp-track-list").innerHTML = songs
      .map((s) => {
        const title = esc(s.title);
        const id = esc(s.id);
        const label = s.hidden ? `Mostrar ${title}` : `Ocultar ${title}`;
        const song = `<span class="mp-track-art"><img src="${cover(s)}" alt="" loading="lazy">${s.hidden ? "" : `<span class="mp-track-glyph">${icon("play")}</span>`}</span>
          <span><strong>${title}</strong><small>${esc(s.artist)}</small></span><time>${clock(s.duration)}</time>`;
        return `<li class="mp-track">
        ${s.hidden ? `<div class="mp-track-song">${song}</div>` : `<button type="button" class="mp-track-song" data-mp="song" data-id="${id}" data-mp-track="${id}">${song}</button>`}
        <div class="mp-track-actions">
          ${s.hidden ? "" : `<button type="button" class="mp-control mp-add" data-mp="enqueue" data-id="${id}" aria-label="Adicionar ${title} à fila" title="Adicionar à fila">${icon("plus")}</button>`}
          <button type="button" class="mp-control" data-mp="visibility" data-id="${id}" aria-label="${label}" title="${label}">${icon(s.hidden ? "eye" : "eyeOff")}</button>
          ${s.kind === "demo" ? "" : `<button type="button" class="mp-control mp-delete" data-mp="delete" data-id="${id}" aria-label="Excluir ${title}" title="Excluir faixa">${icon("trash")}</button>`}
        </div>
      </li>`;
      })
      .join("");
    currentPage.querySelector(".mp-no-songs").hidden = Boolean(songs.length);
    reflectTransport();
  }

  function renderQueue() {
    if (!currentPage) return;
    const pending = queue();
    currentPage.querySelector("[data-mp-queue-count]").textContent = String(
      pending.length,
    );
    currentPage.querySelector(".mp-queue-list").innerHTML = pending
      .map((id) => {
        const s = catalog.get(id);
        return `<li class="mp-queue-entry">
        <button type="button" class="mp-queue-song" data-mp="queued-song" data-id="${esc(id)}"><img src="${cover(s)}" alt="" loading="lazy"><span><strong>${esc(s.title)}</strong><small>${esc(s.artist)}</small></span></button>
        <button type="button" class="mp-control" data-mp="remove" data-id="${esc(id)}" aria-label="Remover ${esc(s.title)} da fila" title="Remover da fila">${icon("x")}</button>
      </li>`;
      })
      .join("");
    currentPage.querySelector(".mp-no-queue").hidden = Boolean(pending.length);
    for (const add of currentPage.querySelectorAll(".mp-add"))
      add.disabled =
        add.dataset.id === currentId || manualQueue.includes(add.dataset.id);
  }

  function fillQueue(afterId) {
    const ids = [...catalog.keys()];
    const rest = shuffled
      ? shuffle(ids.filter((id) => id !== afterId))
      : ids.slice(ids.indexOf(afterId) + 1);
    upcoming = rest.filter((id) => !manualQueue.includes(id));
  }

  function pause() {
    playAttempt++;
    audio.pause();
    reflectTransport();
  }

  function unload() {
    pause();
    if (!loadedId) return;
    loadedId = null;
    audio.removeAttribute("src");
    audio.load();
  }

  function showPlaybackError() {
    if (playbackError || !loadedId) return;
    playbackError = true;
    pause();
    reflectPosition();
    notify(`Não foi possível reproduzir ${current().title}.`);
  }

  function play() {
    if (playingGame || !current()) return;
    beforePlay();
    const attempt = ++playAttempt;
    if (loadedId !== currentId || playbackError) {
      playbackError = false;
      loadedId = currentId;
      audio.src = `/api/songs/${encodeURIComponent(currentId)}/audio`;
    }
    if (audio.ended) audio.currentTime = 0;
    audio.play().catch((error) => {
      if (attempt === playAttempt && error.name !== "AbortError")
        showPlaybackError();
    });
    reflectTransport();
  }

  function takeFromQueue(id) {
    manualQueue = manualQueue.filter((pending) => pending !== id);
    upcoming = upcoming.filter((pending) => pending !== id);
  }

  function switchSong(id, remember = true) {
    if (currentId && id !== currentId && remember) history.push(currentId);
    currentId = id;
    renderSong();
    renderQueue();
    play();
  }

  function next() {
    let id = manualQueue.shift() || upcoming.shift();
    if (!id && repeat === 1) {
      fillQueue(null);
      id = upcoming.shift();
    }
    if (id) switchSong(id);
    else reflectTransport();
  }

  function previous() {
    if (audio.currentTime > 3 || !history.length) {
      if (loadedId && audio.readyState) audio.currentTime = 0;
      return;
    }
    const id = history.pop();
    takeFromQueue(currentId);
    upcoming.unshift(currentId);
    takeFromQueue(id);
    switchSong(id, false);
  }

  function toggle() {
    if (isPlaying()) pause();
    else play();
  }

  function clicked(e) {
    const button = e.target.closest("button[data-mp]");
    if (!button || button.disabled) return;
    const { mp, id } = button.dataset;
    switch (mp) {
      case "toggle":
        toggle();
        break;
      case "next":
        next();
        break;
      case "previous":
        previous();
        break;
      case "open":
        onOpen();
        break;
      case "import":
        onImport();
        break;
      case "hidden":
        viewingHidden = !viewingHidden;
        renderTracks();
        renderQueue();
        break;
      case "visibility":
        onVisibility(id, !hiddenSongs.has(id));
        break;
      case "delete": {
        const song = catalog.get(id) || hiddenSongs.get(id);
        if (song) onDelete(song);
        break;
      }
      case "game":
        if (current()) onGame(current());
        break;
      case "shuffle": {
        shuffled = !shuffled;
        const remaining = new Set(upcoming);
        upcoming = shuffled
          ? shuffle(upcoming)
          : [...catalog.keys()].filter((key) => remaining.has(key));
        renderQueue();
        reflectTransport();
        break;
      }
      case "repeat":
        repeat = (repeat + 1) % 3;
        audio.loop = repeat === 2;
        reflectTransport();
        break;
      case "mute":
        const quiet = audio.muted || audio.volume === 0;
        if (!audio.volume) audio.volume = 0.8;
        audio.muted = !quiet;
        reflectVolume();
        break;
      case "song":
        if (id === currentId) toggle();
        else {
          takeFromQueue(id);
          fillQueue(id);
          switchSong(id);
        }
        break;
      case "queued-song":
        takeFromQueue(id);
        switchSong(id);
        break;
      case "enqueue":
        takeFromQueue(id);
        manualQueue.push(id);
        renderQueue();
        reflectTransport();
        notify("Faixa adicionada à fila.");
        break;
      case "remove":
        takeFromQueue(id);
        renderQueue();
        reflectTransport();
        break;
    }
  }

  dock.addEventListener("click", clicked);
  seek.addEventListener("input", () => {
    audio.currentTime = Number(seek.value);
    reflectPosition();
  });
  volume.addEventListener("input", () => {
    audio.muted = false;
    audio.volume = Number(volume.value) / 100;
  });
  volume.addEventListener("change", () => save({ playerVolume: audio.volume }));
  for (const name of [
    "loadedmetadata",
    "timeupdate",
    "durationchange",
    "emptied",
  ])
    audio.addEventListener(name, reflectPosition);
  for (const name of ["play", "pause"])
    audio.addEventListener(name, reflectTransport);
  audio.addEventListener("ended", next);
  audio.addEventListener("volumechange", reflectVolume);
  audio.addEventListener("error", showPlaybackError);
  reflectVolume();

  return {
    setSongs(songs) {
      const visible = songs.filter((s) => !s.hidden);
      const added = visible.filter((s) => !catalog.has(s.id)).map((s) => s.id);
      catalog = new Map(visible.map((s) => [s.id, s]));
      hiddenSongs = new Map(
        songs.filter((s) => s.hidden).map((s) => [s.id, s]),
      );
      if (!current()) {
        unload();
        currentId = visible[0]?.id ?? null;
        manualQueue = [];
        history = [];
        fillQueue(currentId);
      } else {
        manualQueue = manualQueue.filter((id) => catalog.has(id));
        history = history.filter((id) => catalog.has(id));
        upcoming = upcoming.filter((id) => catalog.has(id)).concat(added);
      }
      renderTracks();
      renderQueue();
      renderSong();
    },
    setScreen(name) {
      if (name !== "player") currentPage = null;
      playingGame = name === "game";
      if (playingGame) pause();
      else reflectTransport();
    },
    mount(view) {
      view.innerHTML = `<div class="music-player">
        <div class="heading mp-heading"><h1>Player</h1><button type="button" class="btn secondary" data-mp="import">${icon("plus")}Adicionar música</button></div>
        <div class="mp-hero-slot"></div>
        <div class="mp-grid">
          <section class="mp-collection" aria-label="Músicas">
            <header class="mp-list-heading"><div><h2>Músicas</h2><span data-mp-count></span></div><div class="mp-filters"><button class="btn quiet" type="button" data-mp="hidden" hidden></button><label class="search">${icon("search")}<input type="search" placeholder="Buscar faixa" aria-label="Buscar faixa" value="${esc(search)}"></label></div></header>
            <ul class="mp-track-list"></ul><p class="mp-empty mp-no-songs" hidden>Nenhuma faixa encontrada.</p>
          </section>
          <section class="mp-queue" aria-label="Fila de reprodução">
            <header class="mp-queue-heading"><h2>Fila de reprodução</h2><span data-mp-queue-count></span></header>
            <ol class="mp-queue-list"></ol><p class="mp-empty mp-no-queue" hidden>Fila vazia</p>
          </section>
        </div>
      </div>`;
      currentPage = view.firstElementChild;
      currentPage.addEventListener("click", clicked);
      currentPage.querySelector('input[type="search"]').oninput = (e) => {
        search = e.target.value;
        renderTracks();
        renderQueue();
      };
      renderTracks();
      renderQueue();
      renderSong();
    },
    pause,
    releaseSong(id) {
      if (loadedId === id) unload();
    },
  };
}
