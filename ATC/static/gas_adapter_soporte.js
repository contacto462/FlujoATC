(function setupGoogleScriptRunAdapterSoporte() {
  async function request(url, options = {}) {
    const hasBody = options && typeof options.body !== "undefined";
    const isFormData =
      hasBody &&
      typeof FormData !== "undefined" &&
      options.body instanceof FormData;
    const baseHeaders = hasBody && !isFormData ? { "Content-Type": "application/json" } : {};
    const response = await fetch(url, {
      credentials: "include",
      headers: {
        ...baseHeaders,
        ...(options.headers || {}),
      },
      ...options,
    });

    if (response.status === 401) {
      window.location.href = "/login";
      const authError = new Error("Sesion expirada. Redirigiendo a login.");
      authError.silent = true;
      throw authError;
    }

    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : await response.text();

    if (!response.ok) {
      const detail = payload && payload.detail ? payload.detail : `HTTP ${response.status}`;
      throw new Error(detail);
    }
    return payload;
  }

  const routes = {
    obtenerRegistrosTabla: () => request("/api/registros/tabla"),
    obtenerListasBBDD: () => request("/api/listas-bbdd"),
    uploadImageForODT: (formData) =>
      request("/api/incidencias/upload-image", {
        method: "POST",
        body: formData,
      }),
    cerrarODT: (formData) =>
      request("/api/incidencias/cerrar-odt", {
        method: "POST",
        body: formData,
      }),
    getUsuarioActual: (token) =>
      request("/api/usuario-actual").then((payload) => {
        if (payload && typeof payload === "object") {
          return payload.usuario || payload.name || payload.username || "Desconocido";
        }
        return payload || "Desconocido";
      }),
    actualizarCelda: (fila, columna, valor, valorOriginal, extraPayload = {}) =>
      request("/api/incidencias/actualizar-celda", {
        method: "POST",
        body: JSON.stringify({
          fila,
          columna,
          valor,
          valor_original: valorOriginal,
          ...extraPayload,
        }),
      }),
    enviarCorreoDerivacionArea: (odt, cliente, derivadoA) =>
      request("/api/incidencias/enviar-correo-derivacion-area", {
        method: "POST",
        body: JSON.stringify({ odt, cliente, derivado_a: derivadoA }),
      }),
  };

  function createRunner(onSuccess, onFailure) {
    return new Proxy(
      {},
      {
        get(_, prop) {
          if (prop === "withSuccessHandler") {
            return (fn) => createRunner(fn, onFailure);
          }
          if (prop === "withFailureHandler") {
            return (fn) => createRunner(onSuccess, fn);
          }
          if (!routes[prop]) {
            return undefined;
          }
          return (...args) => {
            routes[prop](...args)
              .then(onSuccess)
              .catch((error) => {
                if (onFailure) {
                  onFailure(error);
                } else {
                  if (error && error.silent) return;
                  console.error(error);
                  alert(error.message || "Error al ejecutar operacion.");
                }
              });
          };
        },
      }
    );
  }

  window.google = window.google || {};
  window.google.script = window.google.script || {};
  Object.defineProperty(window.google.script, "run", {
    get() {
      return createRunner(
        () => {},
        (error) => {
          if (error && error.silent) return;
          console.error(error);
          alert(error.message || "Error de conexion.");
        }
      );
    },
  });
})();
