(function () {
  function ensureModal() {
    var overlay = document.getElementById("informePreviewOverlay");
    if (overlay) return overlay;

    overlay = document.createElement("div");
    overlay.id = "informePreviewOverlay";
    overlay.className = "informe-preview-overlay";
    overlay.innerHTML = [
      '<div class="informe-preview-modal" role="dialog" aria-modal="true" aria-labelledby="informePreviewTitle">',
      '  <div class="informe-preview-head">',
      '    <div class="informe-preview-title" id="informePreviewTitle">Vista previa</div>',
      '    <div class="informe-preview-actions">',
      '      <a class="informe-preview-download" id="informePreviewDownload" href="#" target="_blank" rel="noopener">Descargar Excel</a>',
      '      <button class="informe-preview-close" type="button" aria-label="Cerrar vista previa">&times;</button>',
      '    </div>',
      '  </div>',
      '  <iframe class="informe-preview-frame" id="informePreviewFrame" title="Vista previa del informe"></iframe>',
      '</div>'
    ].join("");
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) closePreview();
    });
    overlay.querySelector(".informe-preview-close").addEventListener("click", closePreview);

    return overlay;
  }

  function normalizePreviewUrl(url) {
    if (!url) return "";
    try {
      var parsed = new URL(url, window.location.origin);
      if (parsed.pathname.endsWith("/informes") && parsed.searchParams.get("preview")) {
        parsed.pathname = parsed.pathname + "/preview";
        parsed.searchParams.delete("preview");
        return parsed.pathname + parsed.search;
      }
    } catch (_) {}
    return url;
  }

  function openPreview(url, title, downloadUrl) {
    url = normalizePreviewUrl(url);
    if (!url) return;
    var overlay = ensureModal();
    var frame = document.getElementById("informePreviewFrame");
    var titleEl = document.getElementById("informePreviewTitle");
    var download = document.getElementById("informePreviewDownload");

    titleEl.textContent = title || "Vista previa del informe";
    download.href = downloadUrl || url;
    frame.src = url;
    overlay.classList.add("open");
    document.body.classList.add("informe-preview-lock");
  }

  function closePreview() {
    var overlay = document.getElementById("informePreviewOverlay");
    var frame = document.getElementById("informePreviewFrame");
    if (overlay) overlay.classList.remove("open");
    if (frame) frame.src = "about:blank";
    document.body.classList.remove("informe-preview-lock");
  }

  window.abrirVistaPreviaInforme = openPreview;
  window.cerrarVistaPreviaInforme = closePreview;

  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-informe-preview]");
    if (!trigger) return;
    event.preventDefault();
    openPreview(
      trigger.getAttribute("data-informe-preview"),
      trigger.getAttribute("data-informe-title"),
      trigger.getAttribute("data-informe-download")
    );
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closePreview();
  });
})();
