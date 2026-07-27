/**
 * Utilidades JS compartidas entre todos los templates ATC.
 * Cargar con: <script src="/static/utils.js"></script>
 */

async function api(url, options = {}) {
  const opts = {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  };
  const res = await fetch(url, opts);
  const contentType = res.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = typeof payload === "string" ? payload : payload?.detail || JSON.stringify(payload);
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return payload;
}

function normalizarTexto(v) {
  return String(v || "").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/\s+/g, " ").trim().toLowerCase();
}

function formatearRUT(rut) {
  const clean = String(rut || "").trim().replace(/[^0-9Kk]/g, "").toUpperCase();
  if (clean.length < 2) return clean;
  return `${clean.slice(0, -1)}-${clean.slice(-1)}`;
}
