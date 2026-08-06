/**
 * Volver jerarquico por area.
 *
 * El destino no depende del referrer ni de una pila local: el backend calcula
 * el nivel anterior segun la URL actual, el token/LoginSession y los accesos
 * del usuario. Asi una pantalla final vuelve a su seccion/panel padre, y un
 * panel vuelve al selector inicial solo si el usuario tiene mas de un acceso.
 */
(function () {
  var SELECTOR = "#btnVolver, .js-btn-volver";
  var cachedTarget = null;

  function getToken() {
    try {
      var params = new URLSearchParams(location.search);
      var token = params.get("token") || "";
      if (token) {
        sessionStorage.setItem("atcNavToken", token);
        return token;
      }
      return sessionStorage.getItem("atcNavToken") || "";
    } catch (_) {
      return "";
    }
  }

  function fallbackHref(btn) {
    var href = btn && btn.getAttribute && btn.getAttribute("href");
    return href && href !== "#" ? href : "/seleccionar-area";
  }

  function setButtonTarget(btn, target) {
    if (!btn || !target || !target.href) return;
    btn.dataset.atcVolverHref = target.href;
    if (btn.tagName === "A") {
      btn.setAttribute("href", target.href);
    }
    if (target.visible === false) {
      btn.hidden = true;
      btn.setAttribute("aria-hidden", "true");
    } else {
      btn.hidden = false;
      btn.removeAttribute("aria-hidden");
    }
    if (typeof btn.onclick === "function") {
      btn.onclick = null;
      btn.removeAttribute("onclick");
    }
  }

  function resolveTarget() {
    if (cachedTarget) return Promise.resolve(cachedTarget);
    var current = location.pathname + location.search;
    var token = getToken();
    var url = "/api/navigation/volver?current=" + encodeURIComponent(current);
    if (token) url += "&token=" + encodeURIComponent(token);
    return fetch(url, { headers: { "Accept": "application/json" }, credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("No se pudo resolver Volver");
        return res.json();
      })
      .then(function (data) {
        if (!data || !data.href) throw new Error("Destino Volver invalido");
        cachedTarget = { href: data.href, visible: data.visible !== false };
        return cachedTarget;
      });
  }

  function attach(selector) {
    var sel = selector || SELECTOR;
    var buttons = Array.prototype.slice.call(document.querySelectorAll(sel));
    if (!buttons.length) return;

    buttons.forEach(function (btn) {
      if (btn.dataset.atcVolverBound === "1") return;
      btn.dataset.atcVolverBound = "1";
      if (typeof btn.onclick === "function") {
        btn.onclick = null;
        btn.removeAttribute("onclick");
      }
      btn.addEventListener("click", function (event) {
        var href = btn.dataset.atcVolverHref || fallbackHref(btn);
        event.preventDefault();
        location.href = href;
      });
    });

    resolveTarget()
      .then(function (target) {
        buttons.forEach(function (btn) { setButtonTarget(btn, target); });
      })
      .catch(function () {
        buttons.forEach(function (btn) {
          if (typeof btn.onclick === "function") {
            btn.onclick = null;
            btn.removeAttribute("onclick");
          }
        });
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { attach(); });
  } else {
    attach();
  }

  window.ATCVolver = { attach: attach };
})();
