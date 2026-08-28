(function () {
  // Sonidos de notificacion — dos archivos de audio reales (no sintetizados),
  // uno por ticket y otro por incidencia, para que se distingan de oido.
  // Compartido por incidencias_soporte.html y ticketera.html para avisar
  // cuando llega algo nuevo — pedido explicito, ago 2026: UN SOLO sonido
  // (no varios seguidos), sin importar cuántas incidencias/tickets nuevos
  // hayan llegado juntos, y SOLO cuando llega algo — nunca por otra razón
  // (click, navegación, etc.).
  const SONIDOS = {
    ticket: "/static/sonidos/ticket-tin.mp3",
    incidencia: "/static/sonidos/incidencia-pop.mp3",
  };

  // Se crea un <audio> por tipo, una sola vez, y se reutiliza en cada
  // notificacion (reiniciando currentTime a 0) en vez de instanciar uno
  // nuevo cada vez.
  const elementos = {};
  Object.keys(SONIDOS).forEach((tipo) => {
    const audio = new Audio(SONIDOS[tipo]);
    audio.preload = "auto";
    elementos[tipo] = audio;
  });

  // Bloqueo anti-duplicado: en incidencias_soporte.html hay DOS fuentes de
  // detección (poll liviano + poll pesado) que podrían detectar la misma
  // incidencia casi al mismo tiempo y llamar a esta función dos veces para
  // el mismo evento. Sin importar cuántas veces se llame, si ya sonó hace
  // menos de COOLDOWN_MS no vuelve a sonar — pedido explicito, ago 2026
  // ("SOLO UNA VEZ", sin importar cuántas incidencias/tickets se detecten).
  const COOLDOWN_MS = 2500;
  let ultimoDisparo = 0;

  // "ticket" -> ticket-tin.mp3 (ticketera.html), "incidencia" ->
  // incidencia-pop.mp3 (incidencias_soporte.html) — pedido explicito, ago
  // 2026. Los navegadores bloquean el audio hasta la primera interaccion
  // real del usuario en la pagina; si un aviso llega mientras esta
  // bloqueado, ese aviso puntual simplemente no suena — no se agenda para
  // despues, para que no quede pegado a un click sin relacion con la
  // llegada real.
  window.reproducirSonidoNotificacion = function reproducirSonidoNotificacion(tipo) {
    const audio = elementos[tipo] || elementos.ticket;
    if (!audio) return;
    const ahora = Date.now();
    if (ahora - ultimoDisparo < COOLDOWN_MS) return;
    ultimoDisparo = ahora;
    try {
      audio.currentTime = 0;
      const promesa = audio.play();
      if (promesa && typeof promesa.catch === "function") {
        promesa.catch((err) => {
          console.warn("Notificación de sonido: bloqueada por el navegador (sin interacción todavía en esta pestaña) — este aviso puntual no sonó.", err);
        });
      }
    } catch (err) {
      console.warn("Notificación de sonido: no se pudo reproducir.", err);
    }
  };

  // ── Ticker confiable (no se frena en segundo plano) ──────────────────
  // window.iniciarTickerConfiable(ms, callback): corre `callback` cada
  // `ms` milisegundos usando un Web Worker en vez de setInterval del hilo
  // principal, para que el polling de "¿hay algo nuevo?" siga andando a
  // tiempo aunque la pestaña lleve rato en segundo plano — pedido
  // explicito, ago 2026 ("que suene incluso si se tiene abierta hace
  // tiempo ahí"). Si el navegador no soporta Web Workers, cae a un
  // setInterval normal (mejor que nada).
  window.iniciarTickerConfiable = function iniciarTickerConfiable(ms, callback) {
    if (typeof Worker === "undefined") {
      return window.setInterval(callback, ms);
    }
    try {
      const worker = new Worker("/static/atc-notificacion-worker.js");
      worker.onmessage = () => callback();
      worker.onerror = () => {
        // Si el worker falla (ej. bloqueado por alguna politica), no nos
        // quedamos sin polling — seguimos con un setInterval normal.
        worker.terminate();
        window.setInterval(callback, ms);
      };
      worker.postMessage({ ms });
      return worker;
    } catch (_) {
      return window.setInterval(callback, ms);
    }
  };
})();
