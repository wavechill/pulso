import {
  $,
  api,
  clock,
  esc,
  icon,
  post,
  wait,
  audioFileAccept,
} from "./common.js";

const savedPlaylist =
  "https://www.youtube.com/playlist?list=PLxbyexql5ApEtDWl0LF_mHP_GO0XoKp8b";
const working = (job) =>
  ["queued", "running", "saving", "cancelling"].includes(job.status);
const canCancel = (job) =>
  job.videoId && ["queued", "running"].includes(job.status);
function parseLink(text) {
  let u;
  try {
    u = new URL(text.includes("://") ? text : "https://" + text);
  } catch {}
  if (
    !u ||
    ![
      "youtube.com",
      "www.youtube.com",
      "m.youtube.com",
      "music.youtube.com",
      "youtu.be",
    ].includes(u.hostname)
  )
    throw Error("Cole um link de música ou playlist do YouTube.");
  const list = u.searchParams.get("list");
  return {
    url: u.href,
    list:
      list && /^[a-zA-Z0-9_-]+$/.test(list)
        ? "https://www.youtube.com/playlist?list=" + list
        : null,
    whole: u.pathname.replace(/\/$/, "") === "/playlist",
  };
}

export function createImporter({ showModal, notify, getSongs, onReady }) {
  let jobs = [],
    watched = new Set(),
    polling = false,
    uploading = 0,
    uploadErrors = [],
    finished = new Set();
  const cancelling = new Set();
  const dialog = $("#modal");
  function error(e) {
    const el = dialog.open && $("#modal-error");
    if (el) el.textContent = e.message;
    else notify(e.message);
  }
  const guard =
    (fn) =>
    async (...args) => {
      try {
        await fn(...args);
      } catch (e) {
        error(e);
      }
    };
  function open() {
    const previous = jobs.length || uploading || uploadErrors.length;
    showModal(
      "Adicionar música",
      `
      <label class="field">YouTube<input id="url" type="url" placeholder="Cole um link de vídeo ou playlist"></label>
      <div class="actions"><button id="fetch" class="btn primary">${icon("link")} Abrir link</button><button id="linked-playlist" class="btn secondary" hidden>Ver playlist do vídeo</button></div>
      <button class="playlist-shortcut" id="my-playlist"><span class="playlist-icon">${icon("library")}</span><span><strong>Playlist Musicas</strong><small>Escolher faixas da sua playlist</small></span><b aria-hidden="true">›</b></button>
      <div class="divline"></div><label class="upload">${icon("upload")}<strong>Escolher arquivos de áudio</strong><span class="small">Ou arraste suas músicas · até 256 MB por arquivo</span><input class="file-input" id="file" type="file" multiple accept="${audioFileAccept}"></label>
      <button id="see-queue" class="btn quiet wide" ${previous ? "" : "hidden"}>Ver fila de importação</button><div id="modal-error" class="notice"></div>`,
    );
    const url = $("#url"),
      button = $("#fetch"),
      related = $("#linked-playlist");
    url.oninput = () => {
      try {
        const link = parseLink(url.value.trim());
        related.hidden = !link.list || link.whole;
        button.textContent = link.whole ? "Escolher faixas" : "Importar música";
      } catch {
        related.hidden = true;
        button.textContent = "Abrir link";
      }
    };
    button.onclick = guard(async () => {
      const link = parseLink(url.value.trim());
      if (link.whole) await browse(link.url);
      else {
        button.disabled = true;
        try {
          const { job } = await post("import/youtube", { url: link.url });
          watch([job]);
          openQueue();
        } finally {
          button.disabled = false;
        }
      }
    });
    related.onclick = guard(() => browse(parseLink(url.value.trim()).list));
    url.onkeydown = (e) => {
      if (e.key === "Enter") button.click();
    };
    $("#my-playlist").onclick = guard(() => browse(savedPlaylist));
    $("#see-queue").onclick = () => openQueue();
    $("#file").onchange = (e) => upload(Array.from(e.target.files));
    const drop = $(".upload");
    drop.ondragover = (e) => {
      e.preventDefault();
      drop.classList.add("drag");
    };
    drop.ondragleave = () => drop.classList.remove("drag");
    drop.ondrop = (e) => {
      e.preventDefault();
      upload(Array.from(e.dataTransfer.files));
    };
  }
  async function browse(url) {
    showModal(
      "Abrindo playlist",
      '<div class="row" id="listing" style="gap:12px"><span class="busy"></span>Buscando as faixas no YouTube…</div><button class="btn quiet" id="back-import">Voltar</button><div id="modal-error" class="notice"></div>',
    );
    const owner = $("#listing");
    $("#back-import").onclick = open;
    let result;
    try {
      result = await post("import/browse", { url });
    } catch (e) {
      if (owner.isConnected && dialog.open) {
        open();
        error(e);
      }
      return;
    }
    if (!dialog.open || !owner.isConnected) return;
    const { tracks, title } = result;
    const selected = new Set();
    const already = new Set(getSongs().map((s) => s.videoId));
    for (const j of jobs)
      if (canCancel(j) || j.status === "saving") already.add(j.videoId);
    showModal(
      title,
      `<div class="playlist-browser"><p class="small">${tracks.length} faixas · Marque as músicas que deseja baixar.</p>
      <label class="search">${icon("search")}<input type="search" id="pick-search" aria-label="Buscar na playlist" placeholder="Buscar por faixa ou artista"></label>
      <div class="pick-tools"><button id="pick-whole">Selecionar playlist inteira</button><button id="pick-all">Marcar visíveis</button><button id="pick-none">Limpar seleção</button></div>
      <div class="pick-list" id="pick-list"></div><div class="actions"><button class="btn primary" id="pick-import" disabled>Baixar seleção</button><button class="btn quiet" id="pick-back">Voltar</button></div><div id="modal-error" class="notice"></div></div>`,
    );
    const list = $("#pick-list"),
      submit = $("#pick-import"),
      query = $("#pick-search");
    const normalize = (t) =>
      t
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .toLowerCase();
    let visible = [];
    let submitting = false;
    function updateButton() {
      submit.disabled = submitting || !selected.size;
      submit.textContent = submitting
        ? "Adicionando à fila…"
        : selected.size
          ? "Baixar " +
            selected.size +
            (selected.size === 1 ? " faixa" : " faixas")
          : "Baixar seleção";
    }
    function draw() {
      const term = normalize(query.value.trim());
      visible = tracks.filter((t) =>
        normalize(t.title + " " + t.artist).includes(term),
      );
      list.innerHTML =
        visible
          .map(
            (t) =>
              `<label class="pick-track"><input type="checkbox" value="${t.videoId}" ${submitting || already.has(t.videoId) ? "disabled" : ""} ${selected.has(t.videoId) ? "checked" : ""}><span><strong>${esc(t.title)}</strong><small>${esc(t.artist)}</small></span><em>${already.has(t.videoId) ? "Na biblioteca ou na fila" : t.duration ? clock(t.duration) : ""}</em></label>`,
          )
          .join("") || '<div class="empty">Nenhuma faixa encontrada.</div>';
      updateButton();
    }
    list.onchange = (e) => {
      const ch = e.target;
      if (ch.checked) selected.add(ch.value);
      else selected.delete(ch.value);
      updateButton();
    };
    query.oninput = draw;
    $("#pick-all").onclick = () => {
      if (submitting) return;
      for (const t of visible)
        if (!already.has(t.videoId)) selected.add(t.videoId);
      draw();
    };
    $("#pick-none").onclick = () => {
      if (submitting) return;
      selected.clear();
      draw();
    };
    $("#pick-back").onclick = open;
    $("#pick-whole").onclick = () => {
      if (submitting) return;
      for (const t of tracks)
        if (!already.has(t.videoId)) selected.add(t.videoId);
      draw();
    };
    submit.onclick = guard(async () => {
      submitting = true;
      draw();
      const valid = tracks.filter((t) => selected.has(t.videoId));
      try {
        for (let i = 0; i < valid.length; i += 40) {
          const chunk = valid
            .slice(i, i + 40)
            .map(({ videoId, title }) => ({ videoId, title }));
          const batch = await post("import/batch", { tracks: chunk });
          watch(batch.jobs);
        }
        if (submit.isConnected && dialog.open) openQueue();
      } finally {
        submitting = false;
        if (submit.isConnected) draw();
      }
    });
    draw();
  }
  async function upload(files) {
    if (!files.length) return;
    uploading += files.length;
    openQueue();
    for (const f of files) {
      try {
        if (f.size > 256 * 1024 * 1024)
          throw Error("O arquivo ultrapassa 256 MB.");
        const { job } = await api("import/file", {
          method: "POST",
          headers: {
            "Content-Type": "application/octet-stream",
            "X-Filename": encodeURIComponent(f.name),
          },
          body: f,
        });
        watch([job]);
      } catch (e) {
        uploadErrors.push({
          title: f.name,
          message: e.message,
          status: "error",
        });
      } finally {
        uploading--;
        renderQueue();
      }
    }
  }
  function openQueue(mode = "imports") {
    showModal(
      "Fila de importação",
      '<div id="queue-summary" class="small"></div><div id="queue-list" class="queue-list"></div><div class="actions"><button class="btn secondary" id="add-more">Adicionar mais</button><button class="btn primary" id="queue-back">Voltar ao jogo</button></div><div id="modal-error" class="notice"></div>',
    );
    $("#add-more").onclick = open;
    $("#queue-back").onclick = () => dialog.close();
    const list = $("#queue-list");
    list.dataset.mode = mode;
    list.onclick = guard(async (event) => {
      const button = event.target.closest("[data-cancel-download]");
      if (!button || button.disabled) return;
      const id = button.dataset.cancelDownload;
      cancelling.add(id);
      renderQueue();
      try {
        const { job } = await post("import/cancel", { jobId: id });
        jobs = jobs.map((entry) => (entry.id === id ? job : entry));
      } finally {
        cancelling.delete(id);
        renderQueue();
      }
    });
    renderQueue();
  }
  function renderQueue() {
    const list = $("#queue-list");
    if (!list || !dialog.open) return;
    const folder = list.dataset.mode === "folder";
    const tracked = folder
      ? jobs.filter((j) => j.fromMusicFolder)
      : jobs.filter((j) => !j.fromMusicFolder);
    const rows = folder ? tracked : [...tracked, ...uploadErrors];
    const ready = rows.filter((j) => j.status === "done").length,
      errors = rows.filter((j) => j.status === "error").length,
      cancelled = rows.filter((j) => j.status === "cancelled").length;
    $("#queue-summary").textContent = uploading
      ? "Enviando " + uploading + " arquivo(s)…"
      : rows.length
        ? ready +
          " de " +
          rows.length +
          " prontas" +
          (errors ? " · " + errors + " com erro" : "") +
          (cancelled ? " · " + cancelled + " canceladas" : "")
        : "A fila está vazia.";
    const html = rows
      .map(
        (j) =>
          `<div class="queue-item"><span class="queue-dot ${j.status}"></span><div><strong>${esc(j.title || "Arquivo de áudio")}</strong><small>${esc(j.message)}</small></div>${canCancel(j) ? `<button type="button" class="btn quiet queue-cancel" data-cancel-download="${esc(j.id)}" ${cancelling.has(j.id) ? "disabled" : ""} aria-label="Cancelar download de ${esc(j.title)}">${cancelling.has(j.id) ? "Cancelando…" : "Cancelar"}</button>` : ""}</div>`,
      )
      .join("");
    if (list.innerHTML === html) return;
    const focused = document.activeElement?.dataset?.cancelDownload;
    list.innerHTML = html;
    if (focused)
      list
        .querySelector(`[data-cancel-download="${CSS.escape(focused)}"]`)
        ?.focus({ preventScroll: true });
  }
  function watch(ids) {
    if (!polling) {
      watched.clear();
    }
    ids.forEach((id) => watched.add(id));
    monitor();
  }
  async function monitor() {
    if (polling) return;
    polling = true;
    try {
      for (;;) {
        const { jobs: updated } = await api("import/queue");
        jobs = updated;
        const relevant = jobs.filter((j) => watched.has(j.id));
        renderQueue();
        const fresh = relevant.filter(
          (j) => j.status === "done" && !finished.has(j.id),
        );
        if (fresh.length) {
          await onReady(fresh.at(-1).songId);
          fresh.forEach((j) => finished.add(j.id));
        }
        if (
          !uploading &&
          !relevant.some(working) &&
          Array.from(watched).every((id) => jobs.some((j) => j.id === id))
        ) {
          if (
            watched.size === 1 &&
            relevant[0]?.status === "done" &&
            !uploadErrors.length &&
            dialog.open &&
            $("#queue-list")?.dataset.mode === "imports"
          )
            dialog.close();
          if (fresh.length)
            notify(
              fresh.length === 1
                ? "Música pronta para jogar"
                : "Faixas prontas na biblioteca",
            );
          break;
        }
        await wait(950);
      }
    } catch (e) {
      error(e);
    } finally {
      polling = false;
    }
  }
  async function resume() {
    try {
      const result = await api("import/queue");
      jobs = result.jobs;
      const active = jobs.filter((job) => !job.fromMusicFolder && working(job));
      if (active.length) watch(active.map((j) => j.id));
    } catch {
      /* O anfitrião pode ter reiniciado o servidor. */
    }
  }
  async function showQueue() {
    openQueue("folder");
    const list = $("#queue-list");
    try {
      while (list.isConnected && dialog.open) {
        jobs = (await api("import/queue")).jobs;
        renderQueue();
        await wait(950);
      }
    } catch (e) {
      error(e);
    }
  }
  return { open, resume, showQueue };
}
