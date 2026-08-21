// Ticker que corre dentro de un Web Worker — a diferencia de un setInterval
// en el hilo principal, el navegador NO lo frena cuando la pestaña lleva
// rato en segundo plano (Chrome throttlea setInterval del hilo principal a
// ~1 vez por minuto pasados unos minutos oculta la pestaña). Solo avisa
// "tick"; el trabajo real (fetch + sonido) lo hace la pestaña, porque un
// worker no tiene acceso a AudioContext ni al DOM.
let intervalId = null;

self.onmessage = (event) => {
  const ms = event && event.data && event.data.ms;
  if (intervalId) {
    clearInterval(intervalId);
    intervalId = null;
  }
  if (ms) {
    intervalId = setInterval(() => self.postMessage("tick"), ms);
  }
};
