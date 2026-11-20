import { mountLifeFeedback } from "./solo-life.js";
/* Painel do cooperativo. Atualizações vêm dos snapshots do servidor. */
const sheet = document.createElement("link");
sheet.rel = "stylesheet";
sheet.href = new URL("./battle.css", import.meta.url).href;
document.head.append(sheet);

const shapes = {
  heal: '<path d="M8 3v10M3 8h10" stroke-width="3"/>',
  critical:
    '<path d="m9.5 1-7 8h4.3L6 15l7.5-9h-4z" fill="currentColor" stroke="none"/>',
  boost: '<path d="m3 8 5-5 5 5M3 13l5-5 5 5"/>',
  sync: '<path d="m8 2 6 6-6 6-6-6z"/>',
  shield:
    '<path d="M8 1.4 14 4v3.8c0 3.2-2.2 5.5-6 7.1-3.8-1.6-6-3.9-6-7.1V4z"/>',
  heart:
    '<path d="M8 13.8 2.8 9C-1 5.2 3.6.4 7.1 3.5l.9.9.9-.9C12.4.4 17 5.2 13.2 9z" fill="currentColor" stroke="none"/>',
  strike: '<path d="m3 13 9-9M8 3l5 .1V8M2 9l4 4"/>',
};
function symbol(kind) {
  return (
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linejoin="round" stroke-linecap="round" aria-hidden="true">' +
    shapes[kind] +
    "</svg>"
  );
}

const specials = [
  ["heal", "Cura", "+20 de vida · +8 ao aliado com menos vida"],
  ["critical", "Crítico", "3× o dano do acerto"],
  ["boost", "Multiplicador", "2× o dano por 6 s"],
  ["sync", "Sincronia", "Dois acertos juntos causam dano extra"],
];
export function renderBattleLegend() {
  return (
    '<section class="bt-legend" aria-label="Notas especiais"><h3>Notas especiais</h3><div class="bt-legend-items">' +
    specials
      .map(
        ([kind, label, help]) =>
          '<div class="bt-key" title="' +
          help +
          '"><i class="bt-gem bt-' +
          kind +
          '">' +
          symbol(kind) +
          "</i><div><strong>" +
          label +
          "</strong><small>" +
          help +
          "</small></div></div>",
      )
      .join("") +
    "</div></section>"
  );
}

const bosses = [
  {
    name: "Vértice",
    light: "#ded3f5",
    mid: "#a291bb",
    dark: "#514763",
    core: "#ffb58c",
  },
  {
    name: "Cobalto",
    light: "#c3dced",
    mid: "#829eae",
    dark: "#3c536a",
    core: "#a7f4ee",
  },
  {
    name: "Crisol",
    light: "#f4cfab",
    mid: "#ba8d75",
    dark: "#6e4b4a",
    core: "#fadb90",
  },
  {
    name: "Obsidiana",
    light: "#c9d7c1",
    mid: "#8b9d8d",
    dark: "#495955",
    core: "#c0f0a5",
  },
];
function paintGuardian(canvas, palette, shielded) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const size = 240;
  canvas.width = canvas.height = size;
  ctx.setTransform(size / 112, 0, 0, size / 112, 0, 0);
  function shape(points, fill) {
    ctx.fillStyle = fill;
    ctx.beginPath();
    points.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
    ctx.fill();
  }
  function pair(points, color) {
    shape(points, color);
    shape(
      points.map(([x, y]) => [112 - x, y]),
      color,
    );
  }
  const { light, mid, dark, core } = palette;
  const outline = "#25232e";
  // A carapaça tem dois planos facetados. A face escura mantém os olhos legíveis.
  pair(
    [
      [9, 24],
      [23, 33],
      [40, 43],
      [35, 58],
      [19, 51],
    ],
    dark,
  );
  pair(
    [
      [9, 24],
      [20, 30],
      [36, 40],
      [30, 43],
      [15, 37],
    ],
    mid,
  );
  pair(
    [
      [14, 18],
      [24, 23],
      [43, 42],
      [36, 47],
      [21, 35],
    ],
    light,
  );
  pair(
    [
      [16, 63],
      [30, 55],
      [40, 69],
      [32, 85],
      [17, 93],
      [23, 77],
    ],
    dark,
  );
  pair(
    [
      [19, 63],
      [28, 61],
      [33, 68],
      [25, 78],
      [20, 80],
    ],
    mid,
  );
  shape(
    [
      [27, 43],
      [45, 31],
      [56, 25],
      [68, 32],
      [85, 43],
      [83, 65],
      [70, 84],
      [56, 99],
      [42, 84],
      [29, 65],
    ],
    outline,
  );
  pair(
    [
      [27, 43],
      [43, 35],
      [54, 30],
      [47, 48],
      [33, 56],
      [27, 53],
    ],
    mid,
  );
  pair(
    [
      [29, 43],
      [44, 32],
      [54, 27],
      [50, 35],
      [33, 46],
    ],
    light,
  );
  pair(
    [
      [29, 61],
      [36, 60],
      [46, 66],
      [45, 76],
      [36, 72],
    ],
    dark,
  );
  pair(
    [
      [32, 54],
      [46, 50],
      [53, 55],
      [48, 59],
      [41, 58],
    ],
    light,
  );
  pair(
    [
      [33, 57],
      [45, 56],
      [49, 60],
      [46, 63],
      [38, 61],
    ],
    core,
  );
  pair(
    [
      [34, 72],
      [44, 78],
      [51, 85],
      [55, 95],
      [43, 83],
    ],
    mid,
  );
  pair(
    [
      [45, 68],
      [51, 62],
      [55, 69],
      [55, 77],
    ],
    dark,
  );
  shape(
    [
      [56, 34],
      [64, 44],
      [56, 54],
      [48, 44],
    ],
    dark,
  );
  shape(
    [
      [56, 37],
      [61, 44],
      [56, 50],
      [51, 44],
    ],
    core,
  );
  shape(
    [
      [56, 65],
      [61, 72],
      [56, 80],
      [51, 72],
    ],
    light,
  );
  shape(
    [
      [56, 69],
      [58.7, 72],
      [56, 75],
      [53.3, 72],
    ],
    mid,
  );
  if (shielded) {
    ctx.strokeStyle = "#a3deef";
    ctx.lineWidth = 1.3;
    ctx.setLineDash([9, 3]);
    ctx.beginPath();
    [
      [56, 6],
      [100, 27],
      [104, 68],
      [85, 91],
      [56, 106],
      [27, 91],
      [8, 68],
      [12, 27],
    ].forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
    ctx.stroke();
  }
}
const fmt = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
function displayNumber(n) {
  if (n > 0 && n < 1)
    return n.toLocaleString("pt-BR", { maximumFractionDigits: 1 });
  return fmt.format(n);
}
function meter(label, kind, key) {
  return (
    '<div class="bt-meter bt-meter-' +
    kind +
    '" role="progressbar" aria-label="' +
    label +
    '" data-bar="' +
    key +
    '"><i></i></div>'
  );
}
function playerMarkup(slot, me) {
  return (
    '<div class="bt-player" data-slot="' +
    slot +
    '" data-self="' +
    (slot === me) +
    '"><div class="bt-pilot"><strong class="bt-player-name"></strong>' +
    (slot === me ? '<span class="bt-self">Você</span>' : "") +
    '</div><div class="bt-owner">' +
    symbol("heart") +
    '<span class="bt-player-hp"></span><b class="life-delta" aria-hidden="true"></b></div>' +
    meter(slot === me ? "Sua vida" : "Vida do jogador", "pilot", "p" + slot) +
    '<div class="bt-player-stats"><strong class="bt-player-score"></strong><span class="bt-player-effect"></span></div></div>'
  );
}

export function mountBattleHud(el, initial) {
  if (!el) throw Error("Painel da batalha ausente.");
  el.className = "bt-console";
  const slotsInOrder = [
    initial.me,
    ...Array.from(initial.players.keys()).filter((slot) => slot !== initial.me),
  ];
  el.innerHTML =
    '<section class="bt-target" aria-label="Chefe"><div class="bt-target-intro"><div class="bt-portrait"><canvas class="bt-guardian" aria-hidden="true"></canvas></div><div class="bt-boss-id"><span class="bt-overline" data-b="level"></span><h3 data-b="name"></h3><span class="bt-phase" data-b="phase"></span></div></div><div class="bt-resources"><div class="bt-resource-title"><span>' +
    symbol("heart") +
    ' Vida do chefe</span><strong data-b="health"></strong></div>' +
    meter("Vida do chefe", "boss", "boss") +
    '<div class="bt-resource-title bt-shield-label"><span>' +
    symbol("shield") +
    ' Escudo</span><strong data-b="shield"></strong></div>' +
    meter("Escudo do chefe", "shield", "shield") +
    '</div><div class="bt-readout" data-b="readout"><i data-b="event-icon">' +
    symbol("strike") +
    '</i><span data-b="event"></span><span data-b="amount"></span></div></section><section class="bt-party" aria-label="Equipe"><header class="bt-party-heading"><h3>Equipe</h3><span data-b="members"></span></header><div class="bt-player-grid">' +
    slotsInOrder.map((slot) => playerMarkup(slot, initial.me)).join("") +
    "</div></section>" +
    renderBattleLegend();
  const fields = Object.fromEntries(
    Array.from(el.querySelectorAll("[data-b]"), (n) => [n.dataset.b, n]),
  );
  const bars = Object.fromEntries(
    Array.from(el.querySelectorAll("[data-bar]"), (n) => [n.dataset.bar, n]),
  );
  const slots = Array.from(el.querySelectorAll(".bt-player"), (root) => ({
    root,
    index: Number(root.dataset.slot),
    name: root.querySelector(".bt-player-name"),
    hp: root.querySelector(".bt-player-hp"),
    score: root.querySelector(".bt-player-score"),
    effect: root.querySelector(".bt-player-effect"),
    feedback: mountLifeFeedback(root, root.querySelector(".life-delta")),
    previousHp: null,
  }));
  const art = el.querySelector(".bt-guardian");
  const portrait = el.querySelector(".bt-portrait");
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
  let lastId = 0,
    lastSnapshot = -Infinity,
    artState = "",
    animation,
    holdUntil = 0;
  const strings = new WeakMap();
  function setText(node, value) {
    if (strings.get(node) !== value) {
      node.textContent = value;
      strings.set(node, value);
    }
  }
  function writeBar(key, value, max) {
    const node = bars[key];
    max = Math.max(1, max || 1);
    value = Math.min(max, Math.max(0, value || 0));
    if (node._hp === value && node._limit === max) return;
    node._hp = value;
    node._limit = max;
    node.firstElementChild.style.transform = "scaleX(" + value / max + ")";
    node.setAttribute("aria-valuenow", String(Math.round(value)));
    node.setAttribute("aria-valuemax", String(Math.round(max)));
  }
  function report(type, who, amount, target, recipient) {
    const signed = (type === "heal" ? "+" : "−") + displayNumber(amount);
    const hitTarget = target === "shield" ? " no escudo" : " no chefe";
    const actionName = {
      hit: "Dano" + hitTarget + " · " + who,
      critical: "Crítico" + hitTarget + " · " + who,
      sync: "Sincronia" + hitTarget,
      miss: who + " perdeu vida",
      heal: "Cura · " + recipient,
      boost: "Dano 2× · " + who,
      down: who + " em recuperação",
      revive: recipient + " voltou à batalha",
      shieldUp: "Escudo ativado",
      shieldBreak: "Escudo quebrado",
      bossDown: "Chefe derrotado",
    }[type];
    if (!actionName) return;
    fields.readout.dataset.kind = type;
    const kind =
      {
        critical: "critical",
        sync: "sync",
        miss: "heart",
        heal: "heal",
        boost: "boost",
        shieldUp: "shield",
        shieldBreak: "shield",
        revive: "heal",
        down: "heart",
      }[type] || "strike";
    if (fields["event-icon"].dataset.kind !== kind) {
      fields["event-icon"].innerHTML = symbol(kind);
      fields["event-icon"].dataset.kind = kind;
    }
    setText(fields.event, actionName);
    setText(
      fields.amount,
      amount > 0 && ["hit", "critical", "sync", "heal", "miss"].includes(type)
        ? signed
        : "",
    );
  }
  function update(snapshot) {
    if (!snapshot?.boss || snapshot.serverTime < lastSnapshot) return;
    lastSnapshot = snapshot.serverTime;
    const b = snapshot.boss,
      players = snapshot.players,
      me = snapshot.me;
    const level = Math.max(1, b.level || 1),
      palette = bosses[(level - 1) % bosses.length];
    const health = b.health,
      max = b.maxHealth,
      shield = b.shieldHealth || 0,
      shieldMax = b.maxShield || 0;
    setText(fields.level, "Chefe · nível " + level);
    setText(fields.name, palette.name);
    setText(
      fields.phase,
      health <= 0 ? "Derrotado" : "Fase " + (b.phase || 1) + " de 2",
    );
    setText(
      fields.health,
      fmt.format(Math.ceil(health)) + " / " + fmt.format(max),
    );
    setText(
      fields.shield,
      b.shield
        ? fmt.format(Math.ceil(shield)) + " / " + fmt.format(shieldMax)
        : b.phase === 2 && shieldMax > 0
          ? "Quebrado"
          : "Inativo",
    );
    writeBar("boss", health, max);
    writeBar("shield", shield, shieldMax);
    const nextArt = level + "-" + Boolean(b.shield);
    if (nextArt !== artState) {
      paintGuardian(art, palette, b.shield);
      artState = nextArt;
    }
    portrait.dataset.shield = Boolean(b.shield);
    portrait.dataset.defeated = health <= 0;
    const present = players.filter((p) => !p.left).length;
    setText(
      fields.members,
      present + (present === 1 ? " jogador" : " jogadores"),
    );
    for (const slot of slots) {
      const p = players[slot.index];
      const hp = p.health ?? 100,
        maxHp = p.maxHealth || 100;
      if (slot.previousHp !== null && hp !== slot.previousHp)
        slot.feedback.show(hp - slot.previousHp);
      slot.previousHp = hp;
      setText(slot.name, p.name || "Jogador " + (slot.index + 1));
      slot.name.title = slot.name.textContent;
      setText(slot.hp, fmt.format(Math.ceil(hp)) + " / " + fmt.format(maxHp));
      setText(slot.score, fmt.format(p.score || 0) + " pts");
      const state = hp <= 0 ? "down" : hp / maxHp <= 0.25 ? "low" : "up";
      slot.root.dataset.state = state;
      slot.root.dataset.left = Boolean(p.left);
      let effect = "";
      if (p.left) effect = "Saiu";
      else if (state === "down") effect = "Recuperação";
      else if (p.multiplier > 1) {
        const left = Math.min(
          6,
          Math.max(
            0,
            Math.ceil(
              p.boostUntil - (snapshot.serverTime - snapshot.startAt) / 1000,
            ),
          ),
        );
        effect =
          "Dano " + p.multiplier + "×" + (left ? " · " + left + "s" : "");
      } else if (p.powerActive) effect = "Poder ativo";
      else if (state === "low") effect = "Vida baixa";
      setText(slot.effect, effect);
      slot.effect.title =
        state === "down"
          ? "Dano reduzido à metade. Uma nota de cura da equipe recupera sua vida."
          : "";
      writeBar("p" + slot.index, hp, maxHp);
    }
    const batch = (snapshot.events || []).filter((e) => e.id > lastId);
    const now = performance.now();
    if (batch.length) {
      const priority = {
        bossDown: 8,
        shieldBreak: 7,
        revive: 6,
        down: 6,
        shieldUp: 5,
        heal: 4,
        critical: 4,
        sync: 4,
        boost: 4,
        miss: 2,
        hit: 1,
      };
      const event = batch.reduce((a, b) =>
        (priority[b.type] || 0) >= (priority[a.type] || 0) ? b : a,
      );
      lastId = batch.reduce((n, e) => Math.max(n, e.id), lastId);
      const who =
        event.player === me ? "Você" : players[event.player]?.name || "Equipe";
      const recipient =
        event.target === me ? "Você" : players[event.target]?.name || "Equipe";
      // Reter eventos especiais por um instante ajuda a ler o efeito entre batidas.
      if ((priority[event.type] || 0) >= 4 || now >= holdUntil) {
        report(event.type, who, event.amount, event.target, recipient);
        holdUntil = now + ((priority[event.type] || 0) >= 4 ? 880 : 210);
      }
      if (
        !reduceMotion.matches &&
        batch.some((e) =>
          ["hit", "critical", "sync", "shieldBreak"].includes(e.type),
        ) &&
        animation?.playState !== "running"
      ) {
        const critical = batch.some(
          (e) => e.type === "critical" || e.type === "shieldBreak",
        );
        animation = art.animate(
          [
            {
              transform:
                "translateX(-" + (critical ? 3 : 1.7) + "px) scale(1.025)",
            },
            { transform: "none" },
          ],
          { duration: critical ? 240 : 135, easing: "ease-out" },
        );
      }
    } else if (!lastId || (now > holdUntil + 2500 && health > 0)) {
      fields.readout.dataset.kind = b.shield ? "shieldUp" : "idle";
      const k = b.shield ? "shield" : "strike";
      if (fields["event-icon"].dataset.kind !== k) {
        fields["event-icon"].innerHTML = symbol(k);
        fields["event-icon"].dataset.kind = k;
      }
      setText(
        fields.event,
        b.shield
          ? "Acerte em sincronia para romper o escudo"
          : "Acerte as notas para causar dano",
      );
      setText(fields.amount, "");
    }
  }
  update(initial);
  return {
    update,
    destroy() {
      animation?.cancel();
      for (const slot of slots) slot.feedback.destroy();
    },
  };
}
