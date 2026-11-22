const shapes = {
  scenery: '<rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="8" cy="9" r="1.4"/><path d="m4 17 5-5 3 3 3.5-5 5 7"/>',
  folder:
    '<path d="M3 6v13a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1h-8l-2-3H5a2 2 0 0 0-2 2z"/><path d="M3 10h18"/>',
  refresh:
    '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.6 8a7.5 7.5 0 0 1 12-2l2.4 3M18.4 16a7.5 7.5 0 0 1-12 2L4 15"/>',
  library:
    '<rect x="3" y="4" width="4" height="16" rx="1"/><path d="M11 4v16m4-15 4 15 3-1-4-15z"/>',
  users:
    '<circle cx="9" cy="8" r="3"/><path d="M3 19v-2a6 6 0 0 1 12 0v2m1-14a3 3 0 0 1 0 6m2 2c2 .5 3 2 3 5"/>',
  sliders:
    '<path d="M3 7h7m4 0h7M3 17h12m4 0h2"/><circle cx="12" cy="7" r="2"/><circle cx="17" cy="17" r="2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 6.5v6l3.5 2"/>',
  pulse: '<path d="M2 13h4l3-7 5 13 3-8 2 2h3"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  play: '<path d="m7 4 13 8-13 8z" fill="currentColor" stroke="none"/>',
  pause: '<path d="M8 5v14m8-14v14" stroke-width="4"/>',
  previous: '<path d="M5 5v14m14-14-11 7 11 7z"/>',
  next: '<path d="M19 5v14M5 5l11 7-11 7z"/>',
  shuffle:
    '<path d="M3 6h3c5 0 7 12 12 12h3m-3-3 3 3-3 3M3 18h3c5 0 7-12 12-12h3m-3-3 3 3-3 3"/>',
  repeat:
    '<path d="m17 2 4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4m14-1v2a3 3 0 0 1-3 3H3"/>',
  repeatOne:
    '<path d="m17 2 4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4m14-1v2a3 3 0 0 1-3 3H3m7-7 2-1v4"/>',
  mute: '<path d="M3 9h4l5-4v14l-5-4H3zm13 1 5 5m0-5-5 5"/>',
  eye: '<path d="M2 12s3.7-6.5 10-6.5S22 12 22 12s-3.7 6.5-10 6.5S2 12 2 12Z"/><circle cx="12" cy="12" r="2.5"/>',
  eyeOff:
    '<path d="m3 3 18 18M10 5.7c.7-.2 1.3-.2 2-.2 6.3 0 10 6.5 10 6.5s-1.1 1.9-3.2 3.7M6 7.1C3.5 9 2 12 2 12s3.7 6.5 10 6.5c1.4 0 2.6-.3 3.7-.7"/>',
  trash: '<path d="M4 7h16m-14 0 1 13h10l1-13M9 7V4h6v3m-5 3v6m4-6v6"/>',
  music:
    '<path d="M9 17V6l11-2v11M9 9l11-2"/><ellipse cx="6" cy="18" rx="3" ry="2"/><ellipse cx="17" cy="16" rx="3" ry="2"/>',
  disc: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="2"/><path d="M5 11a7 7 0 0 1 5-6"/>',
  x: '<path d="m6 6 12 12M6 18 18 6"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5"/>',
  link: '<path d="m9 15 6-6m-4-2 1-1a5 5 0 1 1 7 7l-2 2M7 9l-2 2a5 5 0 0 0 7 7l2-2"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M15 5V3H3v13h2"/>',
  shield:
    '<path d="m12 3 8 3v5c0 5-4 8-8 10-4-2-8-5-8-10V6z"/><path d="m8 12 3 3 5-6"/>',
  volume:
    '<path d="M3 9h4l5-4v14l-5-4H3zM16 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  keyboard:
    '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M6 9h1m3 0h1m3 0h1m3 0h.1M6 12h1m3 0h1m3 0h1m3 0h.1M8 15h8"/>',
};
export function icon(n) {
  return (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    (shapes[n] || "") +
    "</svg>"
  );
}
export const $ = (s) => document.querySelector(s);
export const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const clock = (t) =>
  Math.floor(t / 60) + ":" + String(Math.floor(t % 60)).padStart(2, "0");
export const diffnames = { easy: "Fácil", medium: "Médio", hard: "Difícil" };
export const audioFileAccept =
  "audio/*,video/mp4,.alac,.ape,.caf,.m4b,.mka,.wave,.wma,.opus,.aif,.aiff,.webm";
export const lanes = {
  easy: [0, 1, 2],
  medium: [0, 1, 2, 3],
  hard: [0, 1, 2, 3, 4],
};
export const wait = (ms) => new Promise((r) => setTimeout(r, ms));
export async function api(path, options = {}) {
  const r = await fetch("/api/" + path, options);
  const data = await r.json();
  if (!r.ok) throw Error(data.message || "Falha ao conectar ao jogo.");
  return data;
}
export function post(path, body = {}, token) {
  return api(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-Player-Token": token } : {}),
    },
    body: JSON.stringify(body),
  });
}
export function prefs() {
  let value = {};
  try {
    value = JSON.parse(localStorage.getItem("pulso.settings")) || {};
  } catch {}
  return {
    keys: ["A", "S", "J", "K", "L"],
    difficulty: "medium",
    volume: 0.8,
    effectsVolume: 0.38,
    gameBackdrop: "art",
    soloMode: "normal",
    name: "",
    ...Object.fromEntries(
      Object.entries(value).filter(([k]) =>
        [
          "keys",
          "difficulty",
          "volume",
          "effectsVolume",
          "gameBackdrop",
          "name",
          "playerVolume",
          "soloMode",
        ].includes(k),
      ),
    ),
  };
}
export function save(p) {
  localStorage.setItem("pulso.settings", JSON.stringify({ ...prefs(), ...p }));
}
