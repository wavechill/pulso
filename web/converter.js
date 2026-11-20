import { api, post, esc, icon, wait, audioFileAccept } from "./common.js";

const formats = [
  ["wav", "WAV", "PCM sem compressão"],
  ["m4a", "M4A", "Áudio AAC"],
  ["mp3", "MP3", "Ampla compatibilidade"],
  ["flac", "FLAC", "Compactação sem perdas"],
  ["ogg", "OGG", "Áudio Vorbis"],
  ["opus", "Opus", "Arquivo compacto"],
];

export function mountConverter(view, songs) {
  view.innerHTML = `
    <section class="converter">
      <div class="heading"><h1>Conversor de áudio</h1></div>
      <form class="panel converter-form">
        <fieldset>
          <legend>Áudio de origem</legend>
          <div class="converter-tabs">
            <button class="filter on" id="source-file" type="button" aria-pressed="true">Arquivo do PC</button>
            <button class="filter" id="source-library" type="button" aria-pressed="false">Biblioteca</button>
          </div>
          <label class="upload converter-upload">
            ${icon("upload")}<strong id="source-name">Escolher áudio</strong>
            <span class="small">Arraste um arquivo ou clique para selecionar</span>
            <input id="converter-file" class="file-input" type="file" accept="${audioFileAccept}">
          </label>
          <label class="field converter-songs" hidden>Faixa da biblioteca
            <select id="converter-song">
              <option value="">Selecione a faixa</option>
              ${songs.map((song) => `<option value="${esc(song.id)}">${esc(song.title)} · ${esc(song.artist)}</option>`).join("")}
            </select>
          </label>
        </fieldset>
        <fieldset>
          <legend>Formato de saída</legend>
          <div class="converter-formats">
            ${formats
              .map(
                ([value, name, detail]) => `
              <label>
                <input type="radio" name="format" value="${value}" ${value === "wav" ? "checked" : ""}>
                <span><strong>${name}</strong><small>${detail}</small></span>
              </label>`,
              )
              .join("")}
          </div>
        </fieldset>
        <div class="actions">
          <button class="btn primary" type="submit" disabled>${icon("refresh")}Converter e salvar</button>
          <button class="btn secondary" id="converter-folder" type="button">${icon("folder")}Abrir pasta de convertidas</button>
        </div>
        <p id="converter-progress" class="small" role="status" hidden></p>
        <div id="converter-result" hidden>
          <p><strong id="converted-name"></strong></p>
          <p class="small">Salvo em Músicas › Convertidas</p>
          <a id="converted-download" class="btn secondary" download>${icon("download")}Baixar cópia</a>
        </div>
        <div id="converter-error" class="notice" role="alert" hidden></div>
      </form>
    </section>`;

  const form = view.querySelector("form");
  const find = (selector) => form.querySelector(selector);
  const input = find("#converter-file"),
    song = find("#converter-song");
  const library = find("#source-library"),
    device = find("#source-file");
  const upload = find(".converter-upload"),
    submit = find("[type=submit]");
  const progress = find("#converter-progress"),
    result = find("#converter-result");
  const error = find("#converter-error");
  let mode = "file",
    chosen,
    busy = false;

  function ready() {
    submit.disabled = busy || !(mode === "file" ? chosen : song.value);
  }
  function pick(file) {
    chosen = file;
    find("#source-name").textContent = file?.name || "Escolher áudio";
    ready();
  }
  function switchSource(type) {
    if (busy) return;
    mode = type;
    for (const [button, active] of [
      [device, type === "file"],
      [library, type === "song"],
    ]) {
      button.classList.toggle("on", active);
      button.setAttribute("aria-pressed", String(active));
    }
    upload.hidden = type !== "file";
    find(".converter-songs").hidden = type !== "song";
    ready();
  }
  function showError(message) {
    error.textContent = message;
    error.hidden = false;
  }
  device.onclick = () => switchSource("file");
  library.onclick = () => switchSource("song");
  input.onchange = () => pick(input.files[0]);
  song.onchange = ready;
  upload.ondragover = (event) => {
    event.preventDefault();
    if (!busy) upload.classList.add("drag");
  };
  upload.ondragleave = () => upload.classList.remove("drag");
  upload.ondrop = (event) => {
    event.preventDefault();
    upload.classList.remove("drag");
    if (!busy && event.dataTransfer.files[0]) pick(event.dataTransfer.files[0]);
  };
  find("#converter-folder").onclick = async () => {
    try {
      await post("converter/open");
    } catch (failure) {
      showError(failure.message);
    }
  };
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    busy = true;
    ready();
    progress.hidden = false;
    progress.textContent = "Preparando o áudio…";
    result.hidden = error.hidden = true;
    const output = form.elements.format.value;
    form
      .querySelectorAll("fieldset")
      .forEach((field) => (field.disabled = true));
    try {
      const { job } =
        mode === "song"
          ? await post("converter/song", { songId: song.value, format: output })
          : await api("converter/file", {
              method: "POST",
              body: chosen,
              headers: {
                "Content-Type": "application/octet-stream",
                "X-Format": output,
                "X-Filename": encodeURIComponent(chosen.name),
              },
            });
      while (form.isConnected) {
        const status = await api("jobs/" + job);
        if (status.status === "error") throw Error(status.message);
        if (status.status === "done") {
          find("#converted-name").textContent = status.fileName;
          find("#converted-download").href = "/api/converter/result/" + job;
          result.hidden = false;
          break;
        }
        progress.textContent = status.message;
        await wait(500);
      }
    } catch (failure) {
      showError(failure.message);
    } finally {
      progress.hidden = true;
      busy = false;
      form
        .querySelectorAll("fieldset")
        .forEach((field) => (field.disabled = false));
      ready();
    }
  };
}
