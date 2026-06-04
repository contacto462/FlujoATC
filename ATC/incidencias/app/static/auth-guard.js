/* auth-guard.js — intercepts 401 API responses and redirects to the right login */
(function () {
  function loginUrlForPath(_path) {
    return "/?form=login&next=auto";
  }

  const _fetch = window.fetch;
  window.fetch = async function (...args) {
    const response = await _fetch(...args);
    if (response.status === 401) {
      const url = typeof args[0] === "string" ? args[0] : args[0]?.url ?? location.pathname;
      try { new URL(url); /* absolute — use current page path */ } catch (_) { /* relative */ }
      const path = new URL(url, location.origin).pathname;
      location.href = loginUrlForPath(path);
    }
    return response;
  };

  const _open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this._atcUrl = url;
    return _open.call(this, method, url, ...rest);
  };
  const _send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...args) {
    this.addEventListener("load", function () {
      if (this.status === 401) {
        const path = new URL(this._atcUrl, location.origin).pathname;
        location.href = loginUrlForPath(path);
      }
    });
    return _send.apply(this, args);
  };
})();
