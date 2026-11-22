const soundsByContext = new WeakMap();
const MISS_DURATION = .096;
const MISS_COOLDOWN = .15;

function synthesize(context, seconds, sample) {
  const rate = context.sampleRate;
  const buffer = context.createBuffer(1, Math.ceil(seconds * rate), rate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < data.length; i++) data[i] = sample(i / rate);
  return buffer;
}

// A palm-muted double stop. Excite two short string delays with a pick and
// filter the result like a small guitar speaker. All buffers are built once.
function mutedStrings(context, pitch, seed) {
  const rate = context.sampleRate;
  const noise = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    return seed / 2147483648 - 1;
  };
  function string(hz, damp) {
    const delay = Float32Array.from({ length: Math.max(3, Math.round(rate / hz)) }, noise);
    let index = 0;
    return () => {
      const next = (index + 1) % delay.length;
      const sample = delay[index];
      delay[index] = damp * (.52 * sample + .48 * delay[next]);
      index = next;
      return sample;
    };
  }
  const first = string(pitch, .959), second = string(pitch * 1.064, .925);
  const lowCut = 1 - Math.exp(-2 * Math.PI * 355 / rate);
  const highCut = 1 - Math.exp(-2 * Math.PI * 3250 / rate);
  let low = 0, body = 0, speaker = 0;
  const buffer = synthesize(context, MISS_DURATION, t => {
    const pick = .16 * noise() * Math.exp(-t * 390);
    const scrape = t < .006 ? 0 : .54 * second() * Math.exp(-42 * (t - .006));
    const contact = Math.tanh(2.4 * (first() * Math.exp(-35 * t) + scrape + pick));
    low += lowCut * (contact - low);
    const upper = contact - low;
    body += lowCut * (upper - body);
    speaker += highCut * (upper - body - speaker);
    return speaker * Math.min(1, t/.0014, (MISS_DURATION-t)/.013);
  });
  const data = buffer.getChannelData(0);
  let peak = 0;
  for (const x of data) peak = Math.max(peak, Math.abs(x));
  const scale = .122 / Math.max(.001, peak);
  for (let j=0; j<data.length; j++) data[j] *= scale;
  return buffer;
}

function gameSounds(context) {
  if (soundsByContext.has(context)) return soundsByContext.get(context);
  const turn = 2 * Math.PI;
  const miss = [mutedStrings(context,294,1501),mutedStrings(context,323,2437),mutedStrings(context,277,3313)];
  let seed = 27183, low = 0, mid = 0;
  const slow = 1 - Math.exp(-turn*105/context.sampleRate);
  const fast = 1 - Math.exp(-turn*3200/context.sampleRate);
  const combo = synthesize(context, .88, t => {
    seed = (Math.imul(seed,1664525) + 1013904223) >>> 0;
    const noise = seed/2147483648 - 1;
    low += slow*(noise-low);
    mid += fast*(noise-mid);
    const crack = Math.exp(-t*57) + (t>.046 ? .52*Math.exp(-(t-.046)*43) : 0);
    const boom = Math.sin(turn*(49*t + .58*(1-Math.exp(-t*36))));
    const decay = Math.exp(-t*4.7) * Math.min(t/.022,1);
    const fade = Math.min(t/.0017,1,(.88-t)/.06);
    return Math.tanh((mid*.85*crack + decay*(.47*boom+low*3.9)) * 1.42) * fade * .85;
  });
  const bank = { miss, combo };
  soundsByContext.set(context, bank);
  return bank;
}

function loadThunder(context, bank) {
  if (bank.loading || bank.original) return;
  bank.loading = true;
  fetch("/api/efeitos/trovao")
    .then(async response => {
      if (!response.ok) return;
      bank.combo = await context.decodeAudioData(await response.arrayBuffer());
      bank.original = true;
    }).catch(() => {}).finally(() => { bank.loading = false; });
}

export function mountGameSounds(context, volume) {
  const gain = context.createGain();
  gain.gain.value = Math.min(1, Math.max(0, volume));
  gain.connect(context.destination);
  const bank = gain.gain.value ? gameSounds(context) : null;
  if (bank) loadThunder(context,bank);
  let lastMiss = -Infinity, variation = 0, thunder, lastThunder = -Infinity, lastNote;
  return {
    play(kind, noteTime) {
      if (!bank) return;
      const strike = kind === "strike" || kind === "combo";
      let buffer = strike ? bank.combo : bank[kind];
      if (strike && thunder && noteTime===lastNote && context.currentTime-lastThunder<.29) {
        if (kind==="combo") thunder.level.gain.setTargetAtTime(1,context.currentTime,.008);
        return;
      }
      if (kind === "miss") {
        if (context.currentTime - lastMiss < MISS_COOLDOWN) return;
        lastMiss = context.currentTime;
        buffer = bank.miss[variation];
        variation = (variation + 1) % bank.miss.length;
      }
      const source = context.createBufferSource();
      source.buffer = buffer;
      if (strike) {
        const t=context.currentTime;
        // A chord lands once in the speakers, and rapid picks cannot stack long tails.
        if (thunder) {
          thunder.level.gain.cancelScheduledValues(t);
          thunder.level.gain.setTargetAtTime(0,t,.012);
          thunder.source.stop(t+.065);
        }
        const level=context.createGain();
        level.gain.value=kind==="combo"?1:.53;
        source.connect(level).connect(gain);
        lastThunder=t;
        lastNote=noteTime;
        thunder={source,level};
        source.onended=()=>{ source.disconnect();level.disconnect();if(thunder?.source===source)thunder=null; };
        source.start();
        return;
      }
      source.connect(gain);
      source.onended = () => source.disconnect();
      source.start();
    },
    destroy() {
      if (thunder) thunder.source.stop();
      gain.disconnect();
    },
  };
}
