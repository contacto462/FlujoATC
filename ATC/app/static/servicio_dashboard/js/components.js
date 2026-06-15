/* components.js — Componentes de UI reutilizables (HTML strings + helpers DOM). */

export function esc(str) {
  return String(str === null || str === undefined ? '' : str)
    .replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = String(val === null || val === undefined ? '—' : val);
}

export function setHTML(id, html) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = html;
}

/* ── Formatos ────────────────────────────────────────────────────── */
export function fmtNum(n) {
  return (n === null || n === undefined) ? '—' : new Intl.NumberFormat('es-CL').format(n);
}
export function fmtPct(n) { return (n === null || n === undefined) ? '—' : n + '%'; }
export function fmtDias(d) {
  if (d === null || d === undefined) return '—';
  if (d < 1) return Math.round(d * 24) + ' h';
  return (Math.round(d * 10) / 10) + ' d';
}
export function fmtFecha(d) {
  return d ? d.toLocaleDateString('es-CL', { day: '2-digit', month: 'short', year: '2-digit' }) : '—';
}
export function fmtMes(key) {
  const parts = key.split('-');
  return new Date(+parts[0], +parts[1] - 1, 1)
    .toLocaleDateString('es-CL', { month: 'short', year: '2-digit' });
}
export function formatLabel(str) {
  if (!str) return 'Sin especificar';
  return str.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

/* ── Badges ──────────────────────────────────────────────────────── */
const ESTADO_BADGE = { finalizada: 'b-green', proceso: 'b-blue', pendiente: 'b-amber', otro: 'b-slate' };
export function badgeEstado(estadoNormalizado, label) {
  return `<span class="badge ${ESTADO_BADGE[estadoNormalizado] || 'b-slate'}">${esc(label)}</span>`;
}

const RESP_BADGE = { 'ATC': 'b-red', 'Cliente': 'b-blue', 'Proveedor Externo': 'b-purple', 'Internet': 'b-teal', 'Otro': 'b-slate' };
export function badgeResp(resp) {
  return `<span class="badge ${RESP_BADGE[resp] || 'b-slate'}">${esc(resp)}</span>`;
}

const CRIT_BADGE = { 'Crítico': 'b-red', 'Medio': 'b-amber', 'Normal': 'b-green' };
export function badgeCriticidad(c) {
  return `<span class="badge ${CRIT_BADGE[c] || 'b-slate'}">${esc(c)}</span>`;
}

const CALIDAD_BADGE = { 'Buena': 'b-green', 'Regular': 'b-amber', 'Mala': 'b-red' };
export function badgeCalidad(c) {
  return `<span class="badge ${CALIDAD_BADGE[c] || 'b-slate'}">${esc(c)}</span>`;
}

export function badgeSlaPct(p) {
  if (p === null || p === undefined) return '<span class="td-dim">—</span>';
  const cls = p >= 80 ? 'b-green' : (p >= 60 ? 'b-amber' : 'b-red');
  return `<span class="badge no-dot ${cls}">${p}%</span>`;
}

/* ── Tendencias (flecha + % vs período anterior) ─────────────────── */
const ARROW_UP = '<svg viewBox="0 0 12 12" fill="none"><path d="M6 9.5v-7M3 5l3-2.8L9 5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const ARROW_DOWN = '<svg viewBox="0 0 12 12" fill="none"><path d="M6 2.5v7M3 7l3 2.8L9 7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

/* dir 'up' es bueno por defecto; con invert=true subir es malo (ej: pendientes) */
export function trendChip(deltaPct, opts) {
  const o = opts || {};
  if (deltaPct === null || deltaPct === undefined) {
    return '<span class="trend flat">— sin comparación</span>';
  }
  if (deltaPct === 0) return '<span class="trend flat">= 0% vs per. anterior</span>';
  const subiendo = deltaPct > 0;
  const bueno = o.invert ? !subiendo : subiendo;
  const cls = bueno ? 'up' : 'down';
  const arrow = subiendo ? ARROW_UP : ARROW_DOWN;
  return `<span class="trend ${cls}">${arrow}${subiendo ? '+' : ''}${deltaPct}% vs per. anterior</span>`;
}

/* ── Ranking de barras horizontal (lista compacta) ───────────────── */
export function rankList(containerId, items, colorVar, max) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const top = items.slice(0, max || 7);
  if (!top.length) {
    el.innerHTML = '<div class="rank-empty">Sin datos en el período</div>';
    return;
  }
  const maxVal = Math.max(...top.map(i => i.count), 1);
  el.innerHTML = top.map(item => `
    <div class="rank-item" style="--rank-color:var(${colorVar})">
      <span class="rank-name" title="${esc(item.label)}">${esc(item.label)}</span>
      <span class="rank-val">${fmtNum(item.count)}</span>
      <div class="rank-bar"><div class="rank-fill" style="width:${Math.round(item.count / maxVal * 100)}%"></div></div>
    </div>`).join('');
}

/* ── Alert card de reincidencia ──────────────────────────────────── */
const NIVEL_COLOR = { 'Amarillo': 'var(--amber)', 'Naranja': '#dd6b20', 'Rojo': 'var(--red)' };

export function alertCard(g, helpers) {
  const { fmtF, badgeE, uniqFn, tecnicosFn } = helpers;
  const tipos = uniqFn(g.odts.map(r => r.problema || '—')).join(', ');
  const tecnicos = uniqFn(g.odts.flatMap(tecnicosFn)).join(', ') || 'Sin asignar';
  const responsables = uniqFn(g.odts.map(r => r.responsable_cierre).filter(Boolean)).join(', ') || 'Sin diagnóstico';
  const dir = g.odts.map(r => r.direccion).find(Boolean) || '';
  const semana = g.semana.split('-W');
  const chips = g.odts
    .slice()
    .sort((a, b) => new Date(a.fecha_registro) - new Date(b.fecha_registro))
    .map(r => `<span class="alert-chip"><b>${esc(r.odt)}</b> ${fmtF(r)} ${badgeE(r)}</span>`)
    .join('');
  return `
  <article class="alert-card" style="--alert-color:${NIVEL_COLOR[g.nivel]}">
    <div class="alert-top">
      <div>
        <div class="alert-name">${esc(g.cliente)}</div>
        <div class="alert-meta">${dir ? esc(dir) + ' · ' : ''}Semana ${esc(semana[1])} de ${esc(semana[0])}</div>
      </div>
      <span class="alert-level">${g.nivel} · ${g.odts.length} ODT</span>
    </div>
    <div class="alert-row"><b>Tipos:</b> ${esc(tipos)}</div>
    <div class="alert-row"><b>Responsable:</b> ${esc(responsables)} &nbsp;·&nbsp; <b>Técnicos:</b> ${esc(tecnicos)}</div>
    <div class="alert-chips">${chips}</div>
  </article>`;
}

/* ── Búsqueda en tablas ──────────────────────────────────────────── */
export function bindTableSearch(inputId, tbodyId) {
  const input = document.getElementById(inputId);
  const tbody = document.getElementById(tbodyId);
  if (!input || !tbody) return;
  input.addEventListener('input', () => {
    const q = input.value.trim().toLowerCase();
    Array.from(tbody.rows).forEach(tr => {
      if (tr.classList.contains('tr-empty')) return;
      tr.style.display = !q || tr.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
  });
}

/* ── CSV export ──────────────────────────────────────────────────── */
export function downloadCSV(filename, headerCells, rows) {
  const cell = v => {
    let s = String(v === null || v === undefined ? '' : v).replace(/\r?\n/g, ' ');
    if (s.includes(';') || s.includes('"')) s = '"' + s.replace(/"/g, '""') + '"';
    return s;
  };
  const lines = [headerCells.join(';')].concat(rows.map(r => r.map(cell).join(';')));
  const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
