/* atc-logo-home.js — el logo ATC lleva al ultimo panel de seleccion visitado.
   Las paginas seleccion_panel_* definen window.ATC_ES_PANEL_SELECTOR = true
   antes de incluir este script, y quedan registradas como "ultimo panel". */
(function () {
  var KEY = "atcUltimoPanel";

  try {
    if (window.ATC_ES_PANEL_SELECTOR) localStorage.setItem(KEY, location.href);
  } catch (_) {}

  function destino() {
    try {
      var v = localStorage.getItem(KEY);
      if (v) return v;
    } catch (_) {}
    var token = new URLSearchParams(location.search).get("token");
    return "/seleccionar-area" + (token ? "?token=" + encodeURIComponent(token) : "");
  }

  function bind() {
    var imgs = document.querySelectorAll('img[src*="logo-atc"]');
    imgs.forEach(function (img) {
      if (img.dataset.atcHomeBound) return;
      img.dataset.atcHomeBound = "1";
      img.style.cursor = "pointer";
      img.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        location.href = destino();
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
