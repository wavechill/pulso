import { icon } from "./common.js";

// The same game layout stays mounted through the break, including fullscreen.
export function mountInterval(panel, { next, finish }) {
  panel.innerHTML =
    '<p class="eyebrow">Intervalo</p><h2>Rodada 1 concluída</h2>' +
    '<dl class="interval-score"><div><dt>Pontos</dt><dd data-total></dd></div>' +
    '<div><dt>Perfeitos</dt><dd data-perfect></dd></div>' +
    '<div><dt>Maior sequência</dt><dd data-best></dd></div></dl>' +
    '<div class="interval-next"><p><strong>Rodada 2</strong><span>1,15×</span></p>' +
    '<p class="muted">A mesma faixa, 15% mais rápida.</p>' +
    '<div class="actions"><button class="btn primary" data-continue>' + icon("play") +
    'Tocar rodada 2</button><button class="btn quiet" data-finish>Finalizar</button></div>' +
    '<p class="interval-status" role="status"></p></div>';
  const button = panel.querySelector("[data-continue]");
  const status = panel.querySelector(".interval-status");
  let pending = null;
  button.onclick = async () => {
    if (pending) return;
    const controller = new AbortController();
    pending = controller;
    button.disabled = true;
    status.textContent = "Preparando o áudio da segunda rodada…";
    try {
      await next(controller.signal);
    } catch {
      if (!controller.signal.aborted) {
        status.textContent = "O áudio não ficou pronto. Tente novamente ou finalize a partida.";
        button.disabled = false;
        button.focus({ preventScroll: true });
      }
    } finally {
      if (pending === controller) pending = null;
    }
  };
  panel.querySelector("[data-finish]").onclick = finish;
  return {
    show({ score, perfect, best }) {
      panel.querySelector("[data-total]").textContent = score.toLocaleString("pt-BR");
      panel.querySelector("[data-perfect]").textContent = perfect;
      panel.querySelector("[data-best]").textContent = best;
      panel.hidden = false;
      button.focus({ preventScroll: true });
    },
    close() {
      pending?.abort();
      pending = null;
      panel.hidden = true;
    },
  };
}
