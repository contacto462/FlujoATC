/**
 * "Volver" como pila de navegación real (no un solo "último referrer" por
 * página). Con un solo valor por página, ir A -> B -> C y volver de C a B
 * hace que B vea a C como su nuevo referrer y se pise el origen real (A),
 * dejando A y B (o B y C) apuntándose entre sí en bucle.
 *
 * Acá se mantiene una pila en sessionStorage con toda la cadena de páginas
 * visitadas. Cada navegación "hacia adelante" (clic en un link normal)
 * apila la página actual; cada clic en Volver desapila un nivel y navega
 * ahí, marcando que el próximo load vino de un Volver para NO volver a
 * apilar (si no, se repetiría el mismo bug).
 *
 * Uso: un solo <script src="/static/atc-volver.js"></script> por página.
 * Se autoadjunta a cualquier #btnVolver y .js-btn-volver — si la pila está
 * vacía, no hace nada y el href fijo del botón funciona como antes.
 */
(function () {
  const STACK_KEY = "atcVolverStack";
  const FLAG_KEY = "atcVolverFlag";

  function leerStack() {
    try {
      const raw = sessionStorage.getItem(STACK_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch (_) {
      return [];
    }
  }

  function guardarStack(stack) {
    try {
      sessionStorage.setItem(STACK_KEY, JSON.stringify(stack));
    } catch (_) {}
  }

  function init() {
    const stack = leerStack();
    const vinoDeVolver = sessionStorage.getItem(FLAG_KEY) === "1";
    sessionStorage.removeItem(FLAG_KEY);

    if (vinoDeVolver) return; // la pila ya quedó correctamente desapilada antes de navegar

    try {
      if (document.referrer) {
        const u = new URL(document.referrer);
        const actual = location.pathname + location.search;
        const origen = u.pathname + u.search;
        if (u.origin === location.origin && origen !== actual && stack[stack.length - 1] !== origen) {
          stack.push(origen);
          guardarStack(stack);
        }
      }
    } catch (_) {}
  }

  function attach(selector) {
    const sel = selector || "#btnVolver, .js-btn-volver";
    document.querySelectorAll(sel).forEach((btn) => {
      btn.addEventListener("click", (e) => {
        const stack = leerStack();
        if (!stack.length) return; // sin pila: deja que el href fijo del boton navegue normal
        const destino = stack.pop();
        guardarStack(stack);
        sessionStorage.setItem(FLAG_KEY, "1");
        e.preventDefault();
        location.href = destino;
      });
    });
  }

  init();
  const autoAttach = () => attach();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", autoAttach);
  } else {
    autoAttach();
  }

  window.ATCVolver = { attach };
})();
