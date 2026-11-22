// Five-fret .chart positions are ticks. Keep the author's timing all the way
// to the audio clock, including sustains that cross a tempo change.
const difficulties = ["Expert", "Hard", "Medium", "Easy"];
const labels = { Expert: "Expert", Hard: "Difícil", Medium: "Médio", Easy: "Fácil" };

function songClock(sync, resolution, offset) {
  const tempos = new Map([[0, 120000]]);
  for (const line of sync) {
    const row = /^(\d+)\s*=\s*B\s+(\d+)$/.exec(line);
    if (!row) continue;
    const tick = Number(row[1]), bpm = Number(row[2]);
    if (!Number.isSafeInteger(tick) || !Number.isSafeInteger(bpm) || bpm <= 0)
      throw Error("O arquivo contém um andamento inválido.");
    tempos.set(tick, bpm);
  }
  // A markers are editor locks. The B markers already encode their timing.
  const changes = [];
  let seconds = offset, previousTick = 0, step = 0;
  for (const [tick, bpm] of [...tempos].sort((a, b) => a[0] - b[0])) {
    seconds += (tick - previousTick) * step;
    step = 60000 / bpm / resolution;
    changes.push({ tick, seconds, step });
    previousTick = tick;
  }
  return tick => {
    let low = 0, high = changes.length;
    while (low + 1 < high) {
      const mid = (low + high) >>> 1;
      if (changes[mid].tick <= tick) low = mid;
      else high = mid;
    }
    const change = changes[low];
    return change.seconds + (tick - change.tick) * change.step;
  };
}

export function readGuitarChart(text) {
  const sections = new Map();
  let rows = null;
  for (const raw of text.split(/\r\n?|\n/)) {
    const line = raw.trim();
    const header = /^\[([^\]]+)\]\s*\{?$/.exec(line);
    if (header) {
      rows = [];
      sections.set(header[1], rows);
    } else if (line === "}") rows = null;
    else if (rows && line && line !== "{") rows.push(line);
  }
  const meta = new Map();
  for (const line of sections.get("Song") || []) {
    const field = /^(\w+)\s*=\s*(.*)$/.exec(line);
    if (field) meta.set(field[1], field[2].replace(/^"(.*)"$/, "$1"));
  }
  const resolution = Number(meta.get("Resolution")), offset = Number(meta.get("Offset") || 0);
  if (!Number.isSafeInteger(resolution) || resolution <= 0 || !Number.isFinite(offset) || !sections.has("SyncTrack"))
    throw Error("Confira o arquivo .chart. Ele precisa conter a resolução e a pista de andamento.");
  const at = songClock(sections.get("SyncTrack"), resolution, offset);
  const tracks = difficulties.filter(d => sections.get(d + "Single")?.some(line => /^\d+\s*=\s*N\s+[0-47]\s/.test(line)));
  if (!tracks.length) throw Error("Este arquivo não tem uma pista de guitarra de cinco teclas.");
  return {
    name: meta.get("Name") || "", charter: meta.get("Charter") || "", tracks, labels,
    playable(difficulty, audioLength) {
      if (!tracks.includes(difficulty)) throw Error("Selecione uma dificuldade que exista no arquivo.");
      const notes = [];
      let lastTick = 0;
      for (const line of sections.get(difficulty + "Single")) {
        if (!/^\d+\s*=\s*N\b/.test(line)) continue;
        const match = /^(\d+)\s*=\s*N\s+(\d+)\s+(\d+)$/.exec(line);
        if (!match) throw Error("Há uma nota com formato inválido nesta dificuldade.");
        const tick = Number(match[1]), lane = Number(match[2]), length = Number(match[3]);
        if (!Number.isSafeInteger(tick + length)) throw Error("Há uma posição de nota inválida no arquivo.");
        if (lane === 7) throw Error("Esta dificuldade usa palhetadas abertas. Escolha uma pista de cinco teclas sem notas abertas.");
        if (lane === 5 || lane === 6) continue; // HOPO and tap modifiers, both played by pressing the fret in Pulso.
        if (lane > 4) throw Error("Esta dificuldade contém um tipo de nota que o trilho não reconhece.");
        const t = at(tick), end = at(tick + length);
        if (t < 0 || t >= audioLength || end > audioLength + .002)
          throw Error("Há notas fora da duração deste áudio. Selecione a gravação usada pelo autor do mapa.");
        notes.push({t, lane, duration: end - t});
        lastTick = Math.max(lastTick, tick + length);
      }
      notes.sort((a, b) => a.t - b.t || a.lane - b.lane);
      for (let i = 1; i < notes.length; i++) {
        if (notes[i].t !== notes[i - 1].t) continue;
        if (notes[i].lane === notes[i - 1].lane) throw Error("O arquivo repete a mesma tecla no mesmo instante.");
        notes[i].chord = notes[i - 1].chord = true;
      }
      const beats = [];
      if (lastTick / resolution < 50000) {
        for (let tick = 0; tick <= lastTick; tick += resolution) if (at(tick) >= 0) beats.push(at(tick));
      }
      return { charts: { hard: notes }, beats, difficultyLabel: "Mapa do arquivo · " + labels[difficulty] };
    }
  };
}
