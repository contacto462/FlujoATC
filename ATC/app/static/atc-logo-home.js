/* Logo ATC: lleva al mismo destino inicial que tendria el usuario al iniciar
   sesion, calculado por backend segun su departament/accesos reales. */
(function () {
  var cachedHref = "";

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

  function fallbackDestino() {
    var token = getToken();
    return token
      ? "/sso/login?token=" + encodeURIComponent(token) + "&next=/seleccionar-area"
      : "/login";
  }

  function resolverDestino() {
    if (cachedHref) return Promise.resolve(cachedHref);
    var token = getToken();
    var url = "/api/navigation/home";
    if (token) url += "?token=" + encodeURIComponent(token);
    return fetch(url, { headers: { "Accept": "application/json" }, credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("No se pudo resolver destino del logo");
        return res.json();
      })
      .then(function (data) {
        cachedHref = data && data.href ? data.href : fallbackDestino();
        return cachedHref;
      })
      .catch(function () {
        cachedHref = fallbackDestino();
        return cachedHref;
      });
  }

  function bind() {
    var imgs = document.querySelectorAll('img[src*="logo-atc"]');
    if (!imgs.length) return;
    resolverDestino().then(function (href) {
      imgs.forEach(function (img) {
        if (img.dataset.atcHomeBound) return;
        img.dataset.atcHomeBound = "1";
        img.style.cursor = "pointer";
        img.setAttribute("role", "link");
        img.setAttribute("tabindex", "0");
        img.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          location.href = cachedHref || href || fallbackDestino();
        });
        img.addEventListener("keydown", function (e) {
          if (e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          location.href = cachedHref || href || fallbackDestino();
        });
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
