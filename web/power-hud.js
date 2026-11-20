/** Leitura de energia e controle de pressão para teclado, mouse e toque. */
export function mountPowerHud(el, { solo = false, onPress, onRelease } = {}) {
  el.classList.add("power-hud");
  el.innerHTML =
    '<div class="power-label"><svg viewBox="0 0 18 18" aria-hidden="true"><path d="M10.5 1.3 3 10h4.7l-.8 6.7L15 7.1h-5z" fill="currentColor"/></svg><strong>Poder</strong><span class="power-value">0%</span><span class="power-effect"></span></div>' +
    '<button type="button" class="power-trigger" aria-pressed="false" aria-label="Segure para ativar o poder"><span class="power-desktop">Segure <kbd>Espaço</kbd></span><span class="power-touch">Segure para ativar</span></button>';
  const button = el.querySelector(".power-trigger");
  button.title =
    "Com 20% de energia, segure para ativar. Solte para guardar energia.";
  const value = el.querySelector(".power-value");
  const effect = el.querySelector(".power-effect");
  const controller = new AbortController();
  const listener = { signal: controller.signal };
  let pointer = null,
    keyboard = null,
    energyLast = -1,
    activeLast = null;
  function release() {
    if (pointer === null && keyboard === null) return;
    pointer = null;
    keyboard = null;
    onRelease?.();
  }
  button.addEventListener(
    "pointerdown",
    (event) => {
      if (pointer !== null || keyboard !== null || event.button !== 0) return;
      event.preventDefault();
      pointer = event.pointerId;
      button.setPointerCapture(pointer);
      onPress?.();
    },
    listener,
  );
  function pointerEnd(event) {
    if (event.pointerId === pointer) release();
  }
  button.addEventListener("pointerup", pointerEnd, listener);
  button.addEventListener("pointercancel", pointerEnd, listener);
  button.addEventListener("lostpointercapture", pointerEnd, listener);
  button.addEventListener(
    "keydown",
    (event) => {
      if (event.code !== "Enter" && event.code !== "Space") return;
      event.preventDefault();
      event.stopPropagation();
      if (keyboard !== null || pointer !== null || event.repeat) return;
      keyboard = event.code;
      onPress?.();
    },
    listener,
  );
  button.addEventListener(
    "keyup",
    (event) => {
      if (event.code !== keyboard) return;
      event.preventDefault();
      event.stopPropagation();
      release();
    },
    listener,
  );
  button.addEventListener(
    "blur",
    () => {
      if (keyboard !== null) release();
    },
    listener,
  );
  window.addEventListener("blur", release, listener);
  document.addEventListener(
    "visibilitychange",
    () => {
      if (document.hidden) release();
    },
    listener,
  );
  function update({ energy = 0, active = false } = {}) {
    const percent = Math.min(100, Math.max(0, Math.round(energy) || 0));
    if (energyLast !== percent || activeLast !== active) {
      button.dataset.ready = String(percent >= 20 || active);
    }
    if (energyLast !== percent) {
      energyLast = percent;
      value.textContent = percent + "%";
    }
    if (activeLast !== active) {
      activeLast = active;
      el.dataset.active = String(active);
      button.setAttribute("aria-pressed", String(active));
      effect.textContent = active ? (solo ? "Pontos ×2" : "Dano ×2") : "";
    }
  }
  update();
  return {
    update,
    destroy() {
      controller.abort();
      release();
    },
  };
}
