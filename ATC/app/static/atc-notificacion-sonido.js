(function () {
  // Sonido de notificacion tipo "tin" (campana, fundamental + armónico,
  // ataque rápido y cola de resonancia larga que va bajando de volumen)
  // via Web Audio API — sin archivo de audio externo. Compartido por
  // incidencias_soporte.html y ticketera.html para avisar cuando llega
  // algo nuevo — pedido explicito, ago 2026: UN SOLO "tin" (no varios
  // seguidos), sin importar cuántas incidencias/tickets nuevos hayan
  // llegado juntos, y SOLO cuando llega algo — nunca por otra razón (click,
  // navegación, etc.).
  //
  // Los navegadores bloquean el audio hasta que hay una interaccion real
  // del usuario en la pagina (click/tecla/touch) — si nunca se interactua
  // con la pestaña, el AudioContext queda "suspended". Antes, si llegaba
  // un aviso mientras estaba bloqueado, el tono quedaba agendado y sonaba
  // recién en un click posterior sin relación real con la llegada (parecía
  // que el sonido lo disparaba el click) — pedido explicito, ago 2026: si
  // está bloqueado en el momento exacto de la llegada, ese aviso puntual
  // simplemente no suena, no queda pegado a una interacción futura.
  const AudioCtor = window.AudioContext || window.webkitAudioContext;
  let ctx = AudioCtor ? new AudioCtor() : null;

  function intentarDestrabar() {
    if (ctx && ctx.state === "suspended") {
      ctx.resume().catch(() => {});
    }
  }
  ["pointerdown", "keydown", "touchstart"].forEach((evento) => {
    document.addEventListener(evento, intentarDestrabar, { passive: true });
  });

  function tono(inicio, frecuencia, volumen, duracion) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = frecuencia;
    gain.gain.setValueAtTime(0, inicio);
    gain.gain.linearRampToValueAtTime(volumen, inicio + 0.008);
    gain.gain.exponentialRampToValueAtTime(0.001, inicio + duracion);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(inicio);
    osc.stop(inicio + duracion + 0.05);
  }

  // Bloqueo anti-duplicado: en incidencias_soporte.html hay DOS fuentes de
  // detección (poll liviano + poll pesado) que podrían detectar la misma
  // incidencia casi al mismo tiempo y llamar a esta función dos veces para
  // el mismo evento. Sin importar cuántas veces se llame, si ya sonó hace
  // menos de COOLDOWN_MS no vuelve a sonar — pedido explicito, ago 2026
  // ("SOLO UNA VEZ", sin importar cuántas incidencias/tickets se detecten).
  const COOLDOWN_MS = 2500;
  let ultimoDisparo = 0;

  window.reproducirSonidoNotificacion = function reproducirSonidoNotificacion() {
    if (!ctx) {
      console.warn("Notificación de sonido: este navegador no soporta Web Audio API.");
      return;
    }
    if (ctx.state === "suspended") {
      // Sin interacción todavía en esta pestaña: no se agenda el sonido
      // para más tarde (sonaría desconectado de la llegada real, pegado a
      // un click cualquiera) — este aviso puntual se pierde y listo.
      ctx.resume().catch(() => {});
      console.warn("Notificación de sonido: bloqueada por el navegador (sin interacción todavía en esta pestaña) — este aviso puntual no sonó.");
      return;
    }
    const ahora = Date.now();
    if (ahora - ultimoDisparo < COOLDOWN_MS) return;
    ultimoDisparo = ahora;
    try {
      const inicio = ctx.currentTime;
      // "Tin" tipo iPhone: fundamental fuerte + armónico mas suave, con
      // cola de resonancia de ~3s que va bajando de volumen — un solo
      // golpe, no varios seguidos.
      tono(inicio, 1760, 0.5, 3.0);
      tono(inicio, 3520, 0.2, 2.2);
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
