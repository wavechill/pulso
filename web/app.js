import {
  $,
  esc,
  clock,
  icon,
  diffnames,
  lanes,
  wait,
  api,
  post,
  prefs,
  save,
} from "./common.js";
import { renderBattleLegend } from "./battle-hud.js";
import { createImporter } from "./import-library.js";
import { mountConverter } from "./converter.js";
import { unlock, prepare, startGame } from "./game.js";
import { createMusicPlayer } from "./player.js";
const view = $("#view"),
  dlg = $("#modal");
let state = { songs: [], level: 1 },
  screen = "library",
  filter = "all",
  search = "",
  selected = "orbita",
  room = null,
  game = null,
  epoch = 0,
  preview = null,
  toastTimer;
document
  .querySelectorAll("[data-icon]")
  .forEach((n) => (n.outerHTML = icon(n.dataset.icon)));
function toast(t) {
  $("#toast").textContent = t;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("#toast").textContent = ""), 3800);
}
function showModal(title, html) {
  dlg.innerHTML =
    '<div class="split"><h2>' +
    esc(title) +
    '</h2><button class="icon-btn" aria-label="Fechar" id="close-modal">' +
    icon("x") +
    "</button></div>" +
    html;
  if (!dlg.open) dlg.showModal();
  $("#close-modal").onclick = () => dlg.close();
}
function err(e) {
  if (dlg.open && $("#modal-error")) $("#modal-error").textContent = e.message;
  else toast(e.message);
}
function guard(fn) {
  return async (...args) => {
    try {
      await fn(...args);
    } catch (e) {
      err(e);
    }
  };
}
function stopPreview() {
  if (preview) {
    preview.pause();
    preview = null;
    const listen = $("#listen");
    if (listen) listen.innerHTML = icon("volume") + "Ouvir prévia";
  }
}
function byId(id) {
  return state.songs.find((s) => s.id === id);
}
function cover(s) {
  return esc(
    s.cover ||
      "/art/" +
        ["orbita", "mare-baixa", "vidro-azul"][s.id.charCodeAt(0) % 3] +
        ".jpg",
  );
}
const player = createMusicPlayer({
  dock: $("#player-dock"),
  cover,
  beforePlay: stopPreview,
  onOpen: () => navigate("player"),
  onGame: (s) => chooseSolo(s),
  onImport: importModal,
  onVisibility: guard(async (id, hidden) => {
    await post(`songs/${encodeURIComponent(id)}/visibility`, { hidden });
    await refresh();
    toast(hidden ? "Faixa ocultada do Player" : "Faixa restaurada no Player");
  }),
  onDelete: confirmSongDeletion,
  notify: toast,
});
function confirmSongDeletion(song) {
  showModal(
    "Excluir música?",
    `${rowCard(song)}<p class="delete-description">Isso apaga o áudio, o mapa e as cópias desta faixa na pasta Músicas do Pulso.</p><div class="actions"><button class="btn secondary" id="keep-song">Cancelar</button><button class="btn danger" id="delete-song">Excluir definitivamente</button></div><div id="modal-error" class="notice"></div>`,
  );
  $("#keep-song").onclick = () => dlg.close();
  $("#keep-song").focus();
  const confirm = $("#delete-song");
  confirm.onclick = guard(async () => {
    confirm.disabled = true;
    player.releaseSong(song.id);
    stopPreview();
    try {
      await post(`songs/${encodeURIComponent(song.id)}/delete`, {
        confirm: true,
      });
      if (confirm.isConnected) dlg.close();
      toast("Faixa excluída");
    } finally {
      if (confirm.isConnected) confirm.disabled = false;
      await refresh();
    }
  });
}
function refresh() {
  return api("state").then((v) => {
    state = v;
    $("#desktop-home").hidden = !window.pulsoDesktop || v.isHost;
    $("#nav-converter").hidden = !v.isHost;
    $("#nav-player").hidden = !v.isHost;
    $("#connection").lastChild.textContent = v.isHost
      ? window.pulsoDesktop
        ? "Neste PC"
        : "Servidor local"
      : "Rede local";
    player.setSongs(v.isHost ? v.songs : []);
    return v;
  });
}
function rowCard(s) {
  return (
    '<div class="song-card"><img src="' +
    cover(s) +
    '" alt=""><div><strong>' +
    esc(s.title) +
    '</strong><span class="small">' +
    esc(s.artist) +
    " · " +
    clock(s.duration) +
    "</span></div></div>"
  );
}
function goNav(name) {
  screen = name;
  player.setScreen(name);
  $("#nav-player").classList.toggle("on", name === "player");
  $("#nav-library").classList.toggle("on", name === "library");
  $("#nav-coop").classList.toggle("on", ["coop", "room"].includes(name));
  $("#nav-converter").classList.toggle("on", name === "converter");
  window.scrollTo(0, 0);
}
function mountPlayer() {
  goNav("player");
  player.mount(view);
  updateMusicFolder();
}
function mountLibrary() {
  goNav("library");
  let s = byId(selected) || state.songs[0];
  if (!s) {
    view.innerHTML =
      '<h1>Biblioteca</h1><button class="btn primary" id="import">Adicionar música</button>';
    $("#import").onclick = importModal;
    return;
  }
  selected = s.id;
  view.innerHTML =
    '<div class="heading"><h1>Biblioteca</h1>' +
    (state.isHost
      ? '<button class="btn secondary" id="import">' +
        icon("plus") +
        "Adicionar música</button>"
      : "") +
    '</div><section class="hero ' +
    (s.title.length > 18 ? "long" : "") +
    '"><div class="hero-copy"><div class="eyebrow">' +
    icon("disc") +
    "Faixa selecionada</div><h2>" +
    esc(s.title) +
    '</h2><p class="artist">' +
    esc(s.artist) +
    '</p><div class="meta"><span>' +
    icon("clock") +
    clock(s.duration) +
    "</span><span>" +
    icon("pulse") +
    Math.round(s.bpm) +
    ' BPM</span><span class="genre">' +
    esc(s.genre || sourceName(s)) +
    '</span></div><div class="actions"><button class="btn primary" id="solo">' +
    icon("play") +
    'Jogar solo</button><button class="btn quiet" id="listen">' +
    icon("volume") +
    "Ouvir prévia</button>" +
    (s.kind === "youtube"
      ? '<a class="save-audio" href="/api/songs/' +
        s.id +
        '/download" download title="Salvar áudio" aria-label="Salvar áudio">' +
        icon("download") +
        "</a>"
      : "") +
    '</div></div><div class="hero-art"><img src="' +
    cover(s) +
    '" alt="Capa da faixa ' +
    esc(s.title) +
    '"></div></section><div class="section-head"><div class="heading-title"><h2>Suas faixas</h2><span class="count">' +
    state.songs.length +
    ' músicas</span></div><label class="search">' +
    icon("search") +
    '<input type="search" id="search" placeholder="Buscar música" aria-label="Buscar música" value="' +
    esc(search) +
    '"></label></div><div class="filters library-controls"><div class="filters">' +
    [
      ["all", "Todas"],
      ["demo", "Originais"],
      ["imports", "Importadas"],
    ]
      .map(
        ([k, v]) =>
          '<button class="filter ' +
          (k === filter ? "on" : "") +
          '" data-filter="' +
          k +
          '">' +
          v +
          "</button>",
      )
      .join("") +
    "</div>" +
    (state.musicFolder
      ? '<div class="folder-actions"><button id="open-music-folder" class="btn secondary">' +
        icon("folder") +
        'Pasta de músicas</button><button id="refresh-music-folder" class="btn secondary">' +
        icon("refresh") +
        "Atualizar músicas</button></div>"
      : "") +
    '</div><div class="folder-progress" id="folder-progress" hidden><span role="status"></span><button class="btn quiet" id="folder-queue">Ver fila</button></div><div class="tracks" id="tracks"></div>';
  $("#import")?.addEventListener("click", importModal);
  $("#solo").onclick = () => chooseSolo(s);
  $("#listen").onclick = guard(async () => {
    let b = $("#listen");
    if (preview) {
      stopPreview();
      return;
    }
    player.pause();
    const a = new Audio("/api/songs/" + s.id + "/audio");
    preview = a;
    a.volume = prefs().volume;
    a.currentTime = Math.min(10, s.duration * 0.12);
    try {
      await a.play();
    } catch (error) {
      if (preview !== a) return;
      stopPreview();
      throw error;
    }
    if (preview !== a) return;
    b.innerHTML = icon("pause") + "Parar prévia";
    const reset = () => {
      if (preview === a) stopPreview();
    };
    a.onended = reset;
    setTimeout(reset, 15000);
  });
  $("#search").oninput = (e) => {
    search = e.target.value;
    tracks();
  };
  view.querySelectorAll("[data-filter]").forEach(
    (b) =>
      (b.onclick = () => {
        filter = b.dataset.filter;
        view
          .querySelectorAll("[data-filter]")
          .forEach((x) => x.classList.toggle("on", x === b));
        tracks();
      }),
  );
  tracks();
  if (state.musicFolder) {
    $("#open-music-folder").onclick = guard(() => post("music-folder/open"));
    $("#refresh-music-folder").onclick = () => updateMusicFolder(true);
    $("#folder-queue").onclick = () => importer.showQueue();
    updateMusicFolder();
  }
}

let checkingMusicFolder = false;
async function updateMusicFolder(manual = false) {
  if (
    !state.musicFolder ||
    !["library", "player"].includes(screen) ||
    checkingMusicFolder
  )
    return;
  checkingMusicFolder = true;
  const refreshButton = $("#refresh-music-folder");
  if (manual) refreshButton.disabled = true;
  try {
    const status = manual
      ? await post("music-folder/scan")
      : await api("music-folder");
    const added = status.revision - state.songs.length;
    if (added) {
      await refresh();
      if (screen === "library" && $("#tracks")) {
        view.querySelector(".heading-title .count").textContent =
          state.songs.length + " músicas";
        tracks();
      }
    }
    if (!["library", "player"].includes(screen)) return;
    const progress = $("#folder-progress");
    if (progress) {
      progress.hidden = !status.pending && !status.failed;
      const label = status.pending
        ? status.pending === 1
          ? "Preparando uma faixa da pasta Músicas…"
          : "Preparando " + status.pending + " faixas da pasta Músicas…"
        : "Confira os arquivos com erro na fila.";
      const message = progress.querySelector("span");
      if (message.textContent !== label) message.textContent = label;
    }
    if (added > 0 && !dlg.open)
      toast(
        added === 1
          ? "Nova música na biblioteca"
          : added + " novas músicas na biblioteca",
      );
    else if (manual && !status.pending) toast("Biblioteca atualizada");
  } catch (error) {
    if (manual) err(error);
  } finally {
    checkingMusicFolder = false;
    if (manual) refreshButton.disabled = false;
  }
}

setInterval(() => {
  if (!document.hidden) updateMusicFolder();
}, 1500);
function sourceName(s) {
  return s.kind === "file"
    ? "Arquivo local"
    : s.kind === "youtube"
      ? "YouTube"
      : "Original";
}
function tracks() {
  const norm = (s) =>
    s
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();
  const songs = state.songs.filter(
    (s) =>
      (filter === "all" || (filter === "demo") === (s.kind === "demo")) &&
      norm(s.title + " " + s.artist).includes(norm(search)),
  );
  $("#tracks").innerHTML =
    '<div class="table-head"><span class="index">#</span><span>Música</span><span class="tag">Estilo / origem</span><span class="bpm">BPM</span><span>' +
    icon("clock") +
    "</span><span></span></div>" +
    songs
      .map(
        (s, i) =>
          '<div class="track"><span class="index">' +
          String(i + 1).padStart(2, "0") +
          '</span><button class="song-name" data-select="' +
          s.id +
          '"><img src="' +
          cover(s) +
          '" alt=""><span><strong>' +
          esc(s.title) +
          "</strong><small>" +
          esc(s.artist) +
          '</small></span></button><span class="tag">' +
          esc(s.genre || sourceName(s)) +
          '</span><span class="bpm">' +
          Math.round(s.bpm) +
          '</span><span class="time">' +
          clock(s.duration) +
          '</span><button class="play-mini" data-play="' +
          s.id +
          '" aria-label="Jogar ' +
          esc(s.title) +
          '">' +
          icon("play") +
          "</button></div>",
      )
      .join("") +
    (songs.length ? "" : '<div class="empty">Nenhuma faixa encontrada.</div>');
  view.querySelectorAll("[data-select]").forEach(
    (b) =>
      (b.onclick = () => {
        stopPreview();
        selected = b.dataset.select;
        mountLibrary();
      }),
  );
  view
    .querySelectorAll("[data-play]")
    .forEach((b) => (b.onclick = () => chooseSolo(byId(b.dataset.play))));
}
function diffs() {
  let d = prefs().difficulty;
  return (
    '<div class="diffs">' +
    Object.entries(diffnames)
      .map(
        ([k, v]) =>
          '<button class="diff ' +
          (k === d ? "on" : "") +
          '" data-diff="' +
          k +
          '">' +
          v +
          "<small>" +
          lanes[k].length +
          " pistas</small></button>",
      )
      .join("") +
    '</div><div class="keyset"></div>'
  );
}
function wireDiff(root) {
  function keys() {
    root.querySelector(".keyset").innerHTML =
      icon("keyboard") +
      " &nbsp;" +
      lanes[prefs().difficulty]
        .map((i) => "<kbd>" + esc(prefs().keys[i]) + "</kbd>")
        .join("");
  }
  keys();
  root.querySelectorAll("[data-diff]").forEach(
    (b) =>
      (b.onclick = () => {
        save({ difficulty: b.dataset.diff });
        root
          .querySelectorAll("[data-diff]")
          .forEach((x) => x.classList.toggle("on", x === b));
        keys();
      }),
  );
}
function chooseSolo(s, returnTo = screen === "player" ? "player" : "library") {
  stopPreview();
  selected = s.id;
  const mode = prefs().soloMode === "practice" ? "practice" : "normal";
  showModal(
    "Jogar solo",
    rowCard(s) +
      '<div class="divline"></div><fieldset class="solo-mode"><legend>Modo</legend>' +
      `<label><input type="radio" name="solo-mode" value="normal" ${mode === "normal" ? "checked" : ""}><span><strong>Normal</strong><small>Reinicia ao zerar a vida</small></span></label>` +
      `<label><input type="radio" name="solo-mode" value="practice" ${mode === "practice" ? "checked" : ""}><span><strong>Treino</strong><small>Toque até o fim</small></span></label>` +
      "</fieldset><strong>Dificuldade</strong>" +
      diffs() +
      '<p class="small play-guide">Toque ao chegar no círculo. Quando a nota tiver uma cauda, mantenha a tecla até ela terminar.</p><button class="btn primary wide" id="start">' +
      icon("play") +
      'Começar</button><div class="notice" id="modal-error"></div>',
  );
  wireDiff(dlg);
  $("#start").onclick = guard(async () => {
    const btn = $("#start");
    const soloMode = dlg.querySelector('[name="solo-mode"]:checked').value;
    save({ soloMode });
    btn.disabled = true;
    btn.textContent = "Preparando áudio…";
    try {
      await unlock();
      const track = await prepare(s);
      if (!dlg.open || !btn.isConnected) return;
      dlg.close();
      goNav("game");
      game = startGame({
        view,
        s,
        track,
        diff: prefs().difficulty,
        practice: soloMode === "practice",
        onEnd: (stats) => result(stats, returnTo),
        onExit: () => navigate(returnTo),
      });
    } finally {
      btn.disabled = false;
      btn.textContent = "Começar";
    }
  });
}
function result(stats, returnTo = "library") {
  game = null;
  goNav("result");
  const { s, hits, total, perfect, best, score, team, practice } = stats;
  view.innerHTML =
    '<div class="panel game-result"><p class="eyebrow">' +
    (team
      ? "Cooperativo · Nível " + team.boss.level
      : practice
        ? "Resultado · Treino"
        : "Resultado solo") +
    '</p><h1 style="margin:12px 0">' +
    (team
      ? team.won
        ? "Chefe derrotado"
        : "Fim da batalha"
      : "Faixa concluída") +
    "</h1>" +
    rowCard(s) +
    (team
      ? '<p class="muted">' +
        (team.won
          ? "Próximo nível liberado."
          : "O chefe terminou com " +
            Math.ceil(
              (100 * team.boss.health) / Math.max(1, team.boss.maxHealth),
            ) +
            "% de vida.") +
        "</p>"
      : "") +
    '<div class="result-metrics">' +
    [
      [score, "Pontos"],
      [Math.round((hits * 100) / Math.max(1, total)) + "%", "Notas acertadas"],
      [best, "Maior sequência"],
      [perfect, "Perfeitos"],
    ]
      .map(
        ([v, l]) =>
          "<div><strong>" +
          v +
          '</strong><span class="small">' +
          l +
          "</span></div>",
      )
      .join("") +
    "</div>" +
    (team
      ? '<div class="battle-results">' +
        team.players
          .map(
            (p, i) =>
              "<div><strong>" +
              esc(p.name) +
              (team.me === i ? " (você)" : "") +
              "</strong><span>" +
              Number(p.score).toLocaleString("pt-BR") +
              " pts · " +
              Math.round(p.damage) +
              " de dano · " +
              Math.ceil(p.health) +
              " de vida</span></div>",
          )
          .join("") +
        "</div>"
      : "") +
    '<div class="actions"><button class="btn primary" id="again">' +
    (team ? "Voltar ao cooperativo" : "Jogar de novo") +
    '</button><button class="btn secondary" id="back">' +
    (returnTo === "player" ? "Voltar ao Player" : "Biblioteca") +
    "</button></div></div>";
  if (team) dropRoom();
  $("#again").onclick = () =>
    team ? navigate("coop") : chooseSolo(s, returnTo);
  $("#back").onclick = () => navigate(returnTo);
}
const importer = createImporter({
  showModal,
  notify: toast,
  getSongs: () => state.songs,
  onReady: async (songId) => {
    await refresh();
    if (screen === "library") {
      stopPreview();
      selected = songId;
      filter = "all";
      search = "";
      mountLibrary();
    }
  },
});
function importModal() {
  importer.open();
}
function settings() {
  const p = prefs();
  showModal(
    "Configurações",
    '<label class="field"><span class="settings-label">Volume <output id="vol-out"></output></span><input id="vol" type="range" min="0" max="100" value="' +
      Math.round(p.volume * 100) +
      '"></label><div class="divline"></div><strong>Teclas das pistas</strong><p class="small">Selecione uma tecla e pressione a nova letra.</p><div class="keyset">' +
      p.keys
        .map(
          (v, i) =>
            '<button class="btn secondary remap" data-key="' +
            i +
            '">' +
            esc(v) +
            "</button>",
        )
        .join("") +
      '</div><div class="notice" id="modal-error"></div><button id="save-settings" class="btn primary wide">Salvar</button>',
  );
  let key = -1;
  const reflect = () => {
    $("#vol-out").value = $("#vol").value + "%";
  };
  reflect();
  $("#vol").oninput = reflect;
  dlg.querySelectorAll(".remap").forEach((b) => {
    b.onclick = () => {
      dlg
        .querySelectorAll(".remap")
        .forEach((x, i) => (x.textContent = p.keys[i]));
      key = +b.dataset.key;
      b.textContent = "…";
    };
    b.onkeydown = (e) => {
      if (key !== +b.dataset.key || !/^Key[A-Z]$/.test(e.code)) return;
      e.preventDefault();
      const ch = e.code.slice(3),
        used = p.keys.indexOf(ch);
      if (used >= 0 && used !== key) p.keys[used] = p.keys[key];
      p.keys[key] = ch;
      key = -1;
      dlg
        .querySelectorAll(".remap")
        .forEach((x, i) => (x.textContent = p.keys[i]));
    };
  });
  $("#save-settings").onclick = () => {
    save({
      keys: p.keys,
      volume: +$("#vol").value / 100,
    });
    dlg.close();
    toast("Configurações salvas");
  };
}
function coop() {
  goNav("coop");
  const chosen = byId(selected) || state.songs[0];
  view.innerHTML =
    '<div class="heading"><h1>Cooperativo</h1></div><div class="coop-grid"><section class="panel"><h2>' +
    icon("shield") +
    'Batalha em equipe</h2><p class="muted">Reúna de 2 a 7 jogadores na mesma rede. Acertem as notas para derrubar o chefe e usem a cura para recuperar a equipe.</p><label class="field">Música<select id="song">' +
    state.songs
      .map(
        (s) =>
          '<option value="' +
          s.id +
          '" ' +
          (s === chosen ? "selected" : "") +
          ">" +
          esc(s.title) +
          " · " +
          esc(s.artist) +
          "</option>",
      )
      .join("") +
    '</select></label><label class="field">Seu nome<input id="name" maxlength="30" placeholder="Jogador" value="' +
    esc(prefs().name) +
    '"></label><strong>Sua dificuldade</strong>' +
    diffs() +
    '<button class="btn primary" id="create">' +
    icon("users") +
    'Criar sala</button></section><section class="panel"><h2>Entrar em uma sala</h2><p class="muted">Cole o convite de quem criou a sala. Se você já estiver no endereço dele, pode digitar só o código.</p><label class="field">Convite ou código<input id="room-code" maxlength="180" autocomplete="off" placeholder="Cole o convite ou digite o código"></label><button class="btn secondary" id="join">Entrar na sala</button></section></div>' +
    renderBattleLegend();
  wireDiff(view);
  $("#create").onclick = guard(async () => {
    let b = $("#create");
    b.disabled = true;
    try {
      const name = $("#name").value.trim() || "Jogador 1";
      save({ name });
      if (!state.lanUrl && state.isHost)
        state.lanUrl = (await post("lan")).lanUrl;
      await openRoom(
        await post("rooms", {
          songId: $("#song").value,
          difficulty: prefs().difficulty,
          name,
        }),
      );
    } finally {
      b.disabled = false;
    }
  });
  $("#join").onclick = guard(() => openInvite($("#room-code").value));
  $("#room-code").onkeydown = (e) => {
    if (e.key === "Enter") $("#join").click();
  };
}
function openInvite(raw) {
  const text = raw.trim();
  if (/^[a-z0-9]{5}$/i.test(text)) return joinPrompt(text);
  let invite;
  try {
    invite = new URL(text);
  } catch {
    throw Error(
      "Cole o convite completo ou digite o código de cinco caracteres.",
    );
  }
  const code = (invite.searchParams.get("sala") || "").toUpperCase();
  const [first, second, ...rest] = invite.hostname.split(".").map(Number);
  const privateNetwork =
    (first === 10 ||
      (first === 192 && second === 168) ||
      (first === 172 && second >= 16 && second <= 31) ||
      (first === 169 && second === 254) ||
      (first === 100 && second >= 64 && second <= 127)) &&
    rest.length === 2 &&
    rest.every((n) => Number.isInteger(n) && n >= 0 && n <= 255);
  if (
    invite.protocol !== "http:" ||
    invite.username ||
    invite.password ||
    invite.pathname !== "/" ||
    !/^[A-Z0-9]{5}$/.test(code) ||
    (!privateNetwork && invite.origin !== location.origin)
  )
    throw Error("Use o convite de uma sala da sua rede local.");
  if (invite.origin === location.origin) return joinPrompt(code);
  const target = invite.origin + "/?sala=" + code;
  if (window.pulsoDesktop) {
    if (!state.isHost)
      throw Error("Volte para Meu Pulso para abrir outro convite.");
    window.chrome.webview.postMessage("join:" + target);
  } else location.assign(target);
}
async function joinPrompt(raw) {
  const code = raw.trim().toUpperCase();
  if (!/^[A-Z0-9]{5}$/.test(code))
    throw Error("Digite o código de cinco caracteres.");
  const info = await api("rooms/" + code + "/info");
  if (info.state !== "lobby" || info.count >= info.limit)
    throw Error("Esta sala já começou, encerrou ou está cheia.");
  showModal(
    "Entrar na sala " + code,
    rowCard(byId(info.songId)) +
      '<label class="field">Seu nome<input id="join-name" maxlength="30" placeholder="Jogador ' +
      (info.count + 1) +
      '" value="' +
      esc(prefs().name) +
      '"></label>' +
      diffs() +
      '<button class="btn primary wide" id="join-yes">Entrar</button><div class="notice" id="modal-error"></div>',
  );
  wireDiff(dlg);
  $("#join-yes").onclick = guard(async () => {
    const b = $("#join-yes");
    b.disabled = true;
    try {
      const name = $("#join-name").value.trim() || `Jogador ${info.count + 1}`;
      save({ name });
      let credential = await post("rooms/" + code + "/join", {
        difficulty: prefs().difficulty,
        name,
      });
      dlg.close();
      await openRoom(credential);
    } finally {
      b.disabled = false;
    }
  });
}
function roomPath(r, a = "") {
  return "rooms/" + r.code + (a ? "/" + a : "");
}
function action(r, a, body = {}) {
  return post(roomPath(r, a), body, r.token);
}
function dropRoom() {
  room = null;
  epoch++;
  history.replaceState(null, "", location.pathname);
}
async function openRoom(credential) {
  stopPreview();
  room = { ...credential, clock: 0 };
  const r = room;
  goNav("room");
  const sequence = ++epoch;
  const url = (state.lanUrl || location.origin) + "/?sala=" + r.code;
  view.innerHTML =
    '<div class="heading"><h1>Sala cooperativa</h1><button class="btn secondary" id="leave">Sair da sala</button></div><div class="coop-grid"><section class="panel"><div class="eyebrow">Código da sala</div><div class="invite">' +
    r.code +
    '</div><label class="field">Convite para a mesma rede<input readonly id="invite"></label><button class="btn secondary" id="copy">' +
    icon("copy") +
    'Copiar convite</button><div class="divline"></div><div id="room-song"></div></section><section class="panel"><div class="split"><h2>Jogadores</h2><strong id="room-capacity" class="room-capacity"></strong></div><div id="roster" class="roster"></div><p id="lobby-status" class="small"></p><div class="actions room-actions"><button class="btn secondary" id="ready">Estou pronto</button><button class="btn primary" id="room-start" hidden disabled>Iniciar partida</button></div></section></div>';
  $("#invite").value = url;
  $("#copy").onclick = guard(async () => {
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      $("#invite").select();
      if (!document.execCommand("copy"))
        throw Error("Selecione o endereço do convite e copie.");
    }
    toast("Convite copiado");
  });
  $("#leave").onclick = () => navigate("coop");
  const btn = $("#ready");
  const start = $("#room-start");
  start.hidden = r.me !== 0;
  start.onclick = guard(async () => {
    start.disabled = true;
    r.starting = true;
    try {
      await action(r, "start");
    } finally {
      r.starting = false;
    }
  });
  btn.disabled = true;
  btn.onclick = guard(async () => {
    btn.disabled = true;
    r.preparing = true;
    btn.textContent = "Preparando áudio…";
    try {
      await unlock();
      r.track = await prepare(byId(r.snapshot.songId));
      const samples = [];
      for (let i = 0; i < 4; i++) {
        const a = Date.now();
        const t = await api("time");
        const b = Date.now();
        samples.push({ rtt: b - a, offset: t.serverTime - (a + b) / 2 });
      }
      r.clock = samples.sort((a, b) => a.rtt - b.rtt)[0].offset;
      await action(r, "ready");
      btn.textContent = "Pronto";
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Tentar novamente";
      throw e;
    } finally {
      r.preparing = false;
    }
  });
  let failures = 0;
  while (sequence === epoch && room === r) {
    try {
      let snap = await api(roomPath(r), {
        headers: { "X-Player-Token": r.token },
      });
      if (room !== r || epoch !== sequence) return;
      r.snapshot = snap;
      failures = 0;
      if (snap.state === "closed") {
        if (game) game.stop();
        game = null;
        dropRoom();
        coop();
        toast("A sala foi encerrada.");
        return;
      }
      if (snap.state === "finished") {
        if (game) game.teamResult(snap);
        return;
      }
      if (snap.state === "lobby") {
        const s = byId(snap.songId);
        $("#room-song").innerHTML = rowCard(s);
        $("#room-capacity").textContent =
          snap.players.length + " / " + snap.limit;
        $("#roster").innerHTML = snap.players
          .map(
            (p, i) =>
              '<div class="member' +
              (i === snap.me ? " self" : "") +
              '"><strong>' +
              esc(p.name) +
              (i === snap.me ? " (você)" : "") +
              (i === 0 ? " <small>Anfitrião</small>" : "") +
              "</strong><span>" +
              diffnames[p.difficulty] +
              " · " +
              (p.ready ? '<b class="room-ready">Pronto</b>' : "Aguardando") +
              "</span></div>",
          )
          .join("");
        const enough = snap.players.length >= snap.minimum;
        const allReady = snap.players.every((p) => p.ready);
        $("#lobby-status").textContent = !enough
          ? "Convide pelo menos mais um jogador."
          : !allReady
            ? "A partida pode começar quando todos estiverem prontos."
            : snap.me === 0
              ? "Equipe pronta. Você pode iniciar a partida."
              : "Equipe pronta. Aguardando o anfitrião.";
        btn.disabled = r.preparing || snap.players[snap.me].ready;
        if (snap.players[snap.me].ready) btn.textContent = "Pronto";
        start.disabled = r.starting || !enough || !allReady;
      }
      if (snap.state === "playing" && !game && r.track) {
        goNav("game");
        game = startGame({
          view,
          s: byId(snap.songId),
          track: r.track,
          diff: snap.players[snap.me].difficulty,
          delay: () => (snap.startAt - Date.now() - r.clock) / 1000,
          room: snap,
          onHits: (indices, holds) => action(r, "hits", { indices, holds }),
          onFinish: () => action(r, "finish"),
          onPower: (active) => action(r, "power", { active }),
          onEnd: result,
          onExit: () => navigate("coop"),
        });
      }
      if (game) game.updateTeam(snap);
    } catch (e) {
      if (room !== r) return;
      if (++failures >= 8) {
        game?.stop();
        game = null;
        dropRoom();
        coop();
        toast("A conexão com a sala caiu.");
        return;
      }
    }
    await wait(game ? 270 : 480);
  }
}
async function navigate(dest) {
  async function commit() {
    if (dlg.open) dlg.close();
    stopPreview();
    if (game) {
      game.stop();
      game = null;
    }
    if (room) {
      const r = room;
      dropRoom();
      action(r, "leave").catch(() => {});
    }
    try {
      if (dest === "home") return window.chrome.webview.postMessage("home");
      await refresh();
      if (dest === "coop") coop();
      else if (dest === "player" && state.isHost) mountPlayer();
      else if (dest === "converter" && state.isHost) {
        goNav("converter");
        mountConverter(view, state.songs);
      } else mountLibrary();
    } catch (e) {
      toast(e.message);
    }
  }
  if (room || game) {
    showModal(
      "Sair da partida?",
      '<p class="muted">' +
        (room
          ? room.me === 0
            ? "A sala será encerrada para todos os jogadores."
            : "A equipe pode continuar jogando."
          : "O progresso desta tentativa será perdido.") +
        '</p><div class="actions" style="margin-top:18px"><button class="btn secondary" id="stay">Continuar jogando</button><button class="btn primary" id="exit">Sair</button></div>',
    );
    $("#stay").onclick = () => dlg.close();
    $("#exit").onclick = commit;
  } else await commit();
}
document
  .querySelectorAll("[data-go]")
  .forEach((b) => (b.onclick = () => navigate(b.dataset.go)));
$("#desktop-home").onclick = () => navigate("home");
$("#settings").onclick = () => {
  if (game || room?.preparing) {
    toast("Ajuste as configurações antes de começar uma partida.");
    return;
  }
  settings();
};
async function boot() {
  try {
    await refresh();
    let code = new URL(location.href).searchParams.get("sala");
    if (code) {
      coop();
      await joinPrompt(code);
    } else if (state.isHost) mountPlayer();
    else mountLibrary();
    if (state.isHost) importer.resume();
  } catch (e) {
    if (!view.childElementCount)
      view.innerHTML =
        '<h1>Servidor indisponível</h1><p class="muted">Abra o jogo pelo iniciador do Pulso.</p>';
    toast(e.message);
  }
}
boot();
