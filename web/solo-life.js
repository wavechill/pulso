const MAX_HEALTH = 100;
const STREAK_FOR_HEAL = 3;

export function mountLifeFeedback(container, label) {
  let timeout;
  function reset() {
    clearTimeout(timeout);
    delete container.dataset.lifeFlash;
    label.textContent = "";
  }
  function show(delta) {
    if (!delta) return;
    const gain = delta > 0;
    container.dataset.lifeFlash = gain ? "heal" : "miss";
    label.textContent = (gain ? "+" : "−") + Math.abs(delta);
    clearTimeout(timeout);
    timeout = setTimeout(reset, 780);
  }
  return { show, destroy: reset };
}

export function mountSoloLife(el, { total, duration, practice }) {
  // A dense chart should take roughly as long to deplete the meter as an easy one.
  const density = Math.max(1, total / Math.max(1, duration));
  const missCost = Math.min(8, Math.max(3, Math.round(11 / density)));
  let value = MAX_HEALTH,
    streak = 0;
  el.classList.add("solo-life");
  el.innerHTML =
    '<div class="sl-head"><div class="sl-label"><svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path d="M10 17.2 3.1 10.7C-1.3 6.4 3.7.3 8.3 4.2L10 5.7l1.7-1.5c4.6-3.9 9.6 2.2 5.2 6.5z"/></svg><strong>Sua vida</strong></div><span class="sl-state"></span><div class="sl-numbers"><b class="life-delta" aria-hidden="true"></b><strong class="sl-value">100</strong><span> / 100</span></div></div>' +
    '<div class="sl-meter" role="progressbar" aria-label="Sua vida" aria-valuenow="100" aria-valuemin="0" aria-valuemax="100"><i></i></div>';
  const meter = el.querySelector(".sl-meter");
  const fill = meter.firstElementChild;
  const number = el.querySelector(".sl-value");
  const status = el.querySelector(".sl-state");
  const feedback = mountLifeFeedback(el, el.querySelector(".life-delta"));
  el.title =
    "Cada nota perdida custa " +
    missCost +
    " pontos de vida. Três acertos seguidos recuperam 6.";
  function render() {
    meter.setAttribute("aria-valuenow", String(value));
    number.textContent = String(value);
    fill.style.transform = "scaleX(" + value / MAX_HEALTH + ")";
    el.dataset.state = value === 0 ? "down" : value <= 25 ? "low" : "up";
    status.textContent =
      value === 0
        ? practice
          ? "Treino ativo"
          : "Vida zerada"
        : value <= 25
          ? "Vida baixa"
          : "";
  }
  function change(delta) {
    const next = Math.max(0, Math.min(MAX_HEALTH, value + delta));
    if (next === value) return;
    feedback.show(next - value);
    value = next;
    render();
  }
  return {
    miss() {
      streak = 0;
      change(-missCost);
    },
    hit() {
      if (value >= MAX_HEALTH) return;
      if (++streak >= STREAK_FOR_HEAL) {
        streak = 0;
        change(6);
      }
    },
    health: () => value,
    reset() {
      value = MAX_HEALTH;
      streak = 0;
      feedback.destroy();
      render();
    },
    destroy() {
      feedback.destroy();
    },
  };
}
