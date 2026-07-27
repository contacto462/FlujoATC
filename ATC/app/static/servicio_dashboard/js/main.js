/* main.js — Orquestador del Dashboard Servicio Técnico.
   Estado global, filtros, render de bloques y navegación. */

import { loadData } from './api.js';
import * as M from './metrics.js';
import * as C from './charts.js?v=2';
import * as UI from './components.js';

/* ── Estado ──────────────────────────────────────────────────────── */
let DATA = []; // Universo completo: incluye ODS de venta para la vista Instalaciones.
let INCIDENCIAS = []; // Solo incidencias: excluye ODS de venta con prefijo V.
let AVANCE = []; // ODS de venta con cámaras a instalar (servicio_tecnico_ventas_odt)
let IS_MOCK = false;
let verTodosClientes = false;

const TOKEN = new URLSearchParams(location.search).get('token') || '';

const FILTERS = {
  desde: '', hasta: '',
  cliente: '', estado: '', tipo: '', tecnico: '', responsable: '', sla: '',
};

/* Reglas de negocio fijas */
const SLA_DIAS = 3;          // SLA técnico: una ODT cumple si cierra en ≤ 3 días
const ODT_ANTIGUA_DIAS = 14; // ODT pendiente antigua: abierta hace ≥ 14 días

/* Las mantenciones preventivas quedan fuera de todos los análisis */
const normTxt = s => (s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
const sinPreventivas = regs => regs.filter(r => !normTxt(r.problema).includes('mantencion preventiva'));
/* Una ODT marcada "Repetida" ya no va a terreno (el operador la duplico y
   soporte/servicio la anulo como tal) — se excluye por completo del analisis. */
const sinRepetidas = regs => regs.filter(r => normTxt(r.estado) !== 'repetida');
const esOdsVenta = r => /^V/i.test(String(r?.odt || '').trim());
const soloIncidencias = regs => regs.filter(r => !esOdsVenta(r));

/* Rango lunes–domingo de la semana actual, en formato YYYY-MM-DD */
function semanaActualISO() {
  const hoy = new Date();
  const dow = hoy.getDay(); // 0 = domingo … 6 = sabado
  const offsetLunes = dow === 0 ? -6 : 1 - dow;
  const lunes = new Date(hoy);
  lunes.setDate(hoy.getDate() + offsetLunes);
  const domingo = new Date(lunes);
  domingo.setDate(lunes.getDate() + 6);
  const iso = d => d.toISOString().slice(0, 10);
  return { desde: iso(lunes), hasta: iso(domingo) };
}

/* ── Bootstrap ───────────────────────────────────────────────────── */
async function bootstrap() {
  try {
    const { registros, config, avance, mock } = await loadData();
    DATA = sinRepetidas(sinPreventivas(registros));
    INCIDENCIAS = soloIncidencias(DATA);
    AVANCE = avance || [];
    IS_MOCK = mock;
    M.setConfig(config);
    M.marcarReincidencias(INCIDENCIAS);

    if (mock) document.getElementById('mockChip').classList.add('visible');

    fillSelects();
    bindControls();
    bindSesion();
    renderAll();
    const t = new Date().toLocaleTimeString('es-CL', { hour: '2-digit', minute: '2-digit' });
    UI.setText('lastUpdate', 'Actualizado ' + t);
  } catch (e) {
    console.error(e);
    alert('No se pudo inicializar el dashboard: ' + e.message);
  } finally {
    document.getElementById('loading').style.display = 'none';
  }
}

/* ── Filtros ─────────────────────────────────────────────────────── */
/* Sin fechas seleccionadas se muestra el histórico completo */
function ventanaFechas() {
  const desde = FILTERS.desde ? new Date(FILTERS.desde + 'T00:00:00') : null;
  const hasta = FILTERS.hasta ? new Date(FILTERS.hasta + 'T23:59:59') : null;
  return { desde, hasta };
}

function pasaFiltrosNoTemporales(r) {
  if (FILTERS.cliente && (r.cliente || '').trim() !== FILTERS.cliente) return false;
  if (FILTERS.estado && (r.estado || '').trim() !== FILTERS.estado) return false;
  if (FILTERS.tipo && (r.problema || '').trim() !== FILTERS.tipo) return false;
  if (FILTERS.tecnico && M.tecnicosDe(r).indexOf(FILTERS.tecnico) === -1) return false;
  if (FILTERS.responsable) {
    if (FILTERS.responsable === '__sin__') { if (r.responsable_cierre) return false; }
    else if ((r.responsable_cierre || '') !== FILTERS.responsable) return false;
  }
  if (FILTERS.sla && M.slaStatus(r, slaObjetivo()) !== FILTERS.sla) return false;
  return true;
}

function filteredFrom(source) {
  const { desde, hasta } = ventanaFechas();
  return source.filter(r => {
    const fr = M.fparse(r.fecha_registro);
    if (desde && (!fr || fr < desde)) return false;
    if (hasta && (!fr || fr > hasta)) return false;
    return pasaFiltrosNoTemporales(r);
  });
}

function filtered() {
  return filteredFrom(INCIDENCIAS);
}

function filteredAll() {
  return filteredFrom(DATA);
}

/* Alertas de Reincidencia: fija siempre a la semana actual (lunes-domingo),
   sin importar el filtro de fechas global — el usuario puede mover el
   filtro de arriba para el resto del panel y esta seccion no se ve afectada. */
function filteredSemanaActual() {
  const { desde, hasta } = semanaActualISO();
  const desdeDt = new Date(desde + 'T00:00:00');
  const hastaDt = new Date(hasta + 'T23:59:59');
  return INCIDENCIAS.filter(r => {
    const fr = M.fparse(r.fecha_registro);
    if (!fr || fr < desdeDt || fr > hastaDt) return false;
    return pasaFiltrosNoTemporales(r);
  });
}

/* Período anterior de igual longitud (para tendencias) */
function filteredPrev() {
  const { desde, hasta } = ventanaFechas();
  if (!desde) return null;
  const fin = hasta || new Date();
  const len = fin - desde;
  if (len <= 0) return null;
  const prevDesde = new Date(desde.getTime() - len);
  return INCIDENCIAS.filter(r => {
    const fr = M.fparse(r.fecha_registro);
    if (!fr || fr < prevDesde || fr >= desde) return false;
    return pasaFiltrosNoTemporales(r);
  });
}

function slaObjetivo() { return SLA_DIAS; }
function umbralAntiguas() { return ODT_ANTIGUA_DIAS; }

function fillSelects() {
  const fill = (id, values, prefix) => {
    const sel = document.getElementById(id);
    sel.innerHTML = `<option value="">${prefix}: todos</option>` +
      values.map(v => `<option value="${UI.esc(v)}">${UI.esc(v)}</option>`).join('');
  };
  fill('fCliente', M.uniq(INCIDENCIAS.map(r => (r.cliente || '').trim())).sort(), 'Cliente');
  fill('fEstado', M.uniq(INCIDENCIAS.map(r => (r.estado || '').trim())).sort(), 'Estado');
  fill('fTipo', M.uniq(INCIDENCIAS.map(r => (r.problema || '').trim())).sort(), 'Tipo');
  fill('fTecnico', M.uniq(INCIDENCIAS.flatMap(M.tecnicosDe)).sort(), 'Técnico');
  const respSel = document.getElementById('fResponsable');
  respSel.innerHTML = '<option value="">Responsable: todos</option>' +
    M.RESPONSABLES.map(v => `<option value="${v}">${v}</option>`).join('') +
    '<option value="__sin__">Sin diagnóstico</option>';
}

function bindControls() {
  const onChange = (id, key) => document.getElementById(id).addEventListener('change', e => { FILTERS[key] = e.target.value; renderAll(); });
  onChange('fCliente', 'cliente'); onChange('fEstado', 'estado'); onChange('fTipo', 'tipo');
  onChange('fTecnico', 'tecnico'); onChange('fResponsable', 'responsable'); onChange('fSla', 'sla');
  onChange('fDesde', 'desde'); onChange('fHasta', 'hasta');

  document.getElementById('btnLimpiar').addEventListener('click', () => {
    Object.assign(FILTERS, { desde: '', hasta: '', cliente: '', estado: '', tipo: '', tecnico: '', responsable: '', sla: '' });
    ['fCliente', 'fEstado', 'fTipo', 'fTecnico', 'fResponsable', 'fSla', 'fDesde', 'fHasta'].forEach(id => { document.getElementById(id).value = ''; });
    renderAll();
  });

  document.getElementById('btnExport').addEventListener('click', exportar);
  document.getElementById('btnRefresh').addEventListener('click', refrescar);
  document.getElementById('btnVerMas').addEventListener('click', () => {
    verTodosClientes = !verTodosClientes;
    document.getElementById('btnVerMas').textContent = verTodosClientes ? 'Ver top 10' : 'Ver todos';
    renderClientes(filtered());
  });

  // Pestañas de dashboard (Servicio / Instalaciones)
  document.querySelectorAll('.view-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.view-tab').forEach(b => b.classList.toggle('active', b === btn));
      document.querySelectorAll('main.content').forEach(v => { v.hidden = v.id !== btn.dataset.view; });
      window.dispatchEvent(new Event('resize')); // ApexCharts reajusta al volver a una vista visible
    });
  });

  // Popup de filtros: toggle con el botón, cierra con clic afuera o Escape
  const filterPanel = document.getElementById('filterPanel');
  const filterAnchor = document.getElementById('filterAnchor');
  document.getElementById('btnFilters').addEventListener('click', () => {
    filterPanel.hidden = !filterPanel.hidden;
  });
  document.addEventListener('click', e => {
    if (!filterPanel.hidden && !filterAnchor.contains(e.target)) filterPanel.hidden = true;
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') filterPanel.hidden = true;
  });

  UI.bindTableSearch('searchClientes', 'tbClientes');
  UI.bindTableSearch('searchAntiguas', 'tbAntiguas');
}

/* ── Sesión: usuario actual + enlace Volver (mismo patrón que las tablas) ── */
function bindSesion() {
  const volver = document.getElementById('btnVolver');
  if (volver && TOKEN) volver.href = '/?form=panelSelectorServicio&token=' + encodeURIComponent(TOKEN);
  cargarUsuarioActual();
}

async function cargarUsuarioActual() {
  if (!TOKEN) { UI.setText('usuarioActualNombre', 'Sin sesión'); return; }
  try {
    const res = await fetch('/api/usuario-actual?token=' + encodeURIComponent(TOKEN), { credentials: 'include' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error('No se pudo cargar usuario');
    UI.setText('usuarioActualNombre', (data && data.usuario) || 'Desconocido');
  } catch (_err) {
    UI.setText('usuarioActualNombre', 'Desconocido');
  }
}

async function refrescar() {
  document.body.classList.add('is-refreshing');
  try {
    const { registros, config, avance, mock } = await loadData();
    DATA = sinRepetidas(sinPreventivas(registros));
    INCIDENCIAS = soloIncidencias(DATA);
    AVANCE = avance || [];
    IS_MOCK = mock;
    M.setConfig(config);
    M.marcarReincidencias(INCIDENCIAS);
    document.getElementById('mockChip').classList.toggle('visible', mock);
    fillSelects();
    renderAll();
    const t = new Date().toLocaleTimeString('es-CL', { hour: '2-digit', minute: '2-digit' });
    UI.setText('lastUpdate', 'Actualizado ' + t);
  } finally {
    document.body.classList.remove('is-refreshing');
  }
}

/* ── Render maestro ──────────────────────────────────────────────── */
function renderAll() {
  const data = filtered();
  const dataInstalaciones = filteredAll();
  const prev = filteredPrev();
  UI.setText('filterMeta', data.length + ' de ' + INCIDENCIAS.length + ' ODT');

  // Contador de filtros activos en el botón del popup
  const activos = ['cliente', 'estado', 'tipo', 'tecnico', 'responsable', 'sla'].filter(k => FILTERS[k]).length;
  const count = document.getElementById('filterCount');
  count.textContent = activos;
  count.hidden = activos === 0;

  renderHero(data, prev);
  renderAnalitica(data);
  renderSlaBlock(data);
  renderClientes(data);
  renderReincidencias(filteredSemanaActual());
  renderDesconexiones(data);
  renderReportes(data);

  // Dashboard 2 · Instalaciones
  renderAvanceCamaras();
  renderInstalaciones(dataInstalaciones);
}

/* ── 1 · Hero ────────────────────────────────────────────────────── */
function renderHero(data, prev) {
  const cur = M.resumen(data, slaObjetivo());
  const old = prev ? M.resumen(prev, slaObjetivo()) : null;
  const alertas = M.calcularAlertas(data);

  UI.setText('heroPeriodo', (FILTERS.desde || FILTERS.hasta) ? 'Rango personalizado' : 'Histórico completo');
  UI.setText('hTotal', UI.fmtNum(cur.total));
  UI.setText('hTasa', UI.fmtPct(cur.tasaResolucion));
  UI.setText('hSla', UI.fmtPct(cur.slaPct));
  UI.setText('hTiempo', UI.fmtDias(cur.tiempoProm));
  UI.setText('hReinc', UI.fmtNum(cur.reinc));
  UI.setText('hAlertas', alertas.length);

  // Sin rango seleccionado no existe "período anterior": se omiten las tendencias
  UI.setHTML('hTotalTrend', old ? UI.trendChip(M.delta(cur.total, old.total), { invert: true }) : `<span class="trend flat">${cur.activas} activas</span>`);
  UI.setHTML('hTasaTrend', old ? UI.trendChip(M.delta(cur.tasaResolucion, old.tasaResolucion)) : `<span class="trend flat">${UI.fmtNum(cur.fin)} finalizadas</span>`);
  UI.setHTML('hSlaTrend', old ? UI.trendChip(M.delta(cur.slaPct, old.slaPct)) : `<span class="trend ${cur.slaFuera ? 'down' : 'up'}">${UI.fmtNum(cur.slaFuera)} fuera de SLA</span>`);
  UI.setHTML('hTiempoTrend',
    (old && cur.tiempoProm !== null && old.tiempoProm !== null)
      ? UI.trendChip(M.delta(Math.round(cur.tiempoProm * 10), Math.round(old.tiempoProm * 10)), { invert: true })
      : `<span class="trend flat">objetivo ${SLA_DIAS} días</span>`);
  UI.setHTML('hReincTrend', old ? UI.trendChip(M.delta(cur.reinc, old.reinc), { invert: true }) : `<span class="trend flat">ventana ${M.getConfig().reincidencia_ventana_dias} días</span>`);
  const nivelMax = alertas.length ? alertas[0].nivel : null;
  UI.setHTML('hAlertasTrend', nivelMax
    ? `<span class="trend ${nivelMax === 'Rojo' ? 'down' : 'flat'}">Nivel máximo: ${nivelMax}</span>`
    : '<span class="trend up">Sin reincidencias semanales</span>');
}

/* ── 2 · Bloque analítico ────────────────────────────────────────── */
function renderAnalitica(data) {
  const p = C.themePalette();
  const meses = M.mesesDe(data);
  const cats = meses.map(UI.fmtMes);

  C.areaChart('chEvolucion', cats, [
    { name: 'Total', data: M.serieMensual(data, meses) },
    { name: 'Finalizadas', data: M.serieMensual(data, meses, M.isFinal) },
    { name: 'Pendientes', data: M.serieMensual(data, meses, M.isPend) },
    { name: 'En proceso', data: M.serieMensual(data, meses, M.isProc) },
    { name: 'Reincidencias', data: M.serieMensual(data, meses, r => r._reincidente) },
  ], { colors: [p.slate, p.green, p.amber, p.blue, p.red], height: 300 });

  const estados = M.countBy(data, r => r.estado || 'Sin estado');
  const estadoColor = label => {
    const e = label.toLowerCase();
    if (e.includes('termin') || e.includes('final')) return p.green;
    if (e.includes('proceso')) return p.blue;
    if (e.includes('pend')) return p.amber;
    return p.slate;
  };
  C.donutChart('chEstados', estados.map(e => e.label), estados.map(e => e.count),
    estados.map(e => estadoColor(e.label)), { totalLabel: 'ODT', height: 300 });

  const conDiag = data.filter(r => M.isFinal(r) && r.responsable_cierre);
  const rc = C.respColors();
  const resp = M.RESPONSABLES
    .map(x => ({ label: x, count: conDiag.filter(r => r.responsable_cierre === x).length }))
    .filter(x => x.count > 0);
  C.donutChart('chResp', resp.map(x => x.label), resp.map(x => x.count),
    resp.map(x => rc[x.label]), { totalLabel: 'Diagnósticos' });

  const tipos = M.countBy(data, r => r.problema || 'Sin tipo').slice(0, 8);
  C.hbarChart('chTipos', tipos.map(t => t.label), tipos.map(t => t.count), p.brand, { name: 'ODT' });
}

/* ── 4 · SLA ─────────────────────────────────────────────────────── */
function renderSlaBlock(data) {
  const p = C.themePalette();
  const objetivo = slaObjetivo();
  UI.setText('slaRegla', `SLA técnico estándar: una ODT cumple SLA si se cierra dentro de ${objetivo} días. Las ODT abiertas ya vencidas cuentan como incumplidas; las abiertas en plazo no penalizan.`);

  const evaluables = data.filter(r => { const s = M.slaStatus(r, objetivo); return s === 'cumplida' || s === 'incumplida'; });
  const dentro = evaluables.filter(r => M.slaStatus(r, objetivo) === 'cumplida');
  const fuera = evaluables.filter(r => M.slaStatus(r, objetivo) === 'incumplida');
  const slaPct = evaluables.length ? Math.round(dentro.length / evaluables.length * 100) : null;

  C.radialSla('chSlaRadial', slaPct, 'Cumplimiento SLA');
  UI.setText('slaDentro', UI.fmtNum(dentro.length));
  UI.setText('slaFuera', UI.fmtNum(fuera.length));
  UI.setText('slaEnPlazo', UI.fmtNum(data.filter(r => M.slaStatus(r, objetivo) === 'en_plazo').length));

  const meses = M.mesesDe(evaluables);
  const slaSerie = meses.map(m => {
    const ms = evaluables.filter(r => M.monthKey(r.fecha_registro) === m);
    if (!ms.length) return 0;
    return Math.round(ms.filter(r => M.slaStatus(r, objetivo) === 'cumplida').length / ms.length * 100);
  });
  C.areaChart('chSlaMensual', meses.map(UI.fmtMes),
    [{ name: '% cumplimiento', data: slaSerie }],
    { colors: [p.purple], max: 100, suffix: '%' });

  const porTipo = M.grupoPromedioCierre(data.filter(M.isFinal), r => r.problema || 'Sin tipo').slice(0, 8);
  C.hbarChart('chTiempoTipo', porTipo.map(g => g.label), porTipo.map(g => M.round1(g.avg)), p.blue, { name: 'Días promedio', suffix: ' días' });

  // Tabla: ODT fuera de plazo
  const rows = fuera
    .slice()
    .sort((a, b) => (M.isFinal(b) ? M.cierreDias(b) : M.diasAbierta(b)) - (M.isFinal(a) ? M.cierreDias(a) : M.diasAbierta(a)))
    .slice(0, 30);
  UI.setHTML('tbFueraPlazo', rows.map(r => {
    const dias = M.isFinal(r) ? M.cierreDias(r) : M.diasAbierta(r);
    return `<tr>
      <td class="td-mono">${UI.esc(r.odt)}</td>
      <td class="td-main">${UI.esc(r.cliente)}</td>
      <td>${UI.esc(r.problema || '—')}</td>
      <td>${UI.badgeEstado(M.estadoNorm(r), r.estado)}</td>
      <td>${UI.esc(r.tecnicos || '—')}</td>
      <td><span class="badge no-dot b-red">${UI.fmtDias(dias)}</span></td>
    </tr>`;
  }).join('') || '<tr class="tr-empty"><td colspan="6" class="td-empty">Sin ODT fuera de plazo en el período 🎉</td></tr>');
  UI.setText('lblFueraPlazo', fuera.length + ' ODT superaron el objetivo de ' + objetivo + ' día(s)' + (fuera.length > 30 ? ' · mostrando 30' : ''));
}

/* ── 5 · Clientes ────────────────────────────────────────────────── */
function renderClientes(data) {
  const stats = M.clienteStats(data, slaObjetivo());
  const inst = M.calcularInstalaciones(data, INCIDENCIAS);
  inst.filter(i => i.calidad === 'Mala').forEach(i => {
    const s = stats.find(x => x.cliente === i.cliente);
    if (s) s.instMala++;
  });
  const umbral = M.umbralVolumen(stats);

  const visibles = verTodosClientes ? stats : stats.slice(0, 10);
  UI.setHTML('tbClientes', visibles.map((s, i) => `
    <tr>
      <td class="td-dim">${i + 1}</td>
      <td class="td-main">${UI.esc(s.cliente)}</td>
      <td><b>${s.total}</b></td>
      <td>${s.fin}</td>
      <td>${s.pend || 0}</td>
      <td>${s.proc || 0}</td>
      <td>${s.tiempos.length ? UI.fmtDias(M.avg(s.tiempos)) : '—'}</td>
      <td>${s.diag ? UI.fmtPct(M.pct(s.atc, s.diag)) : '—'}</td>
      <td>${s.diag ? UI.fmtPct(M.pct(s.cli, s.diag)) : '—'}</td>
      <td>${s.reinc ? `<span class="badge no-dot b-red">${s.reinc}</span>` : '0'}</td>
      <td>${UI.badgeSlaPct(s.slaEval ? Math.round(s.slaOk / s.slaEval * 100) : null)}</td>
      <td>${UI.badgeCriticidad(M.criticidad(s, umbral))}</td>
    </tr>`).join('') || '<tr class="tr-empty"><td colspan="12" class="td-empty">Sin datos en el período</td></tr>');
  UI.setText('lblClientes', stats.length + ' clientes con ODT en el período');

  const criticos = stats.filter(s => M.criticidad(s, umbral) !== 'Normal')
    .map(s => ({ label: s.cliente, count: s.total }));
  UI.rankList('rankCriticos', criticos, '--red', 7);
}

/* ── Dashboard 2 · Avance de instalación de cámaras ──────────────── */
/* Fuente: servicio_tecnico_ventas_odt + venta_ods ("Cámaras a instalar").
   Instalación finalizada ⇒ todas las cámaras; el avance parcial
   (camaras_instaladas) lo poblará el sistema de cierre más adelante. */
function renderAvanceCamaras() {
  const items = AVANCE.map(a => {
    const total = Number(a.camaras_total) > 0 ? Number(a.camaras_total) : null;
    const rawInstaladas = a.camaras_instaladas !== null && a.camaras_instaladas !== undefined
      ? Math.max(0, Number(a.camaras_instaladas) || 0)
      : 0;
    const fin = !!a.finalizada || (total !== null && rawInstaladas >= total);
    let instaladas = null;
    if (fin) instaladas = total; // finalizada ⇒ todas las cámaras
    else if (total !== null) instaladas = Math.min(total, rawInstaladas);
    let pct = null;
    if (fin) pct = 100;
    else if (total && instaladas !== null) pct = Math.min(100, Math.round(instaladas / total * 100));
    return { a, total, instaladas, pct, fin };
  }).sort((x, y) => (x.fin - y.fin) || ((y.pct || 0) - (x.pct || 0))); // en curso primero

  const enCurso = items.filter(i => !i.fin).length;
  const finalizadas = items.length - enCurso;
  const totalCamaras = items.reduce((s, i) => s + (i.total || 0), 0);
  const totalInstaladas = items.reduce((s, i) => s + (i.instaladas || 0), 0);
  const totalPendientes = Math.max(0, totalCamaras - totalInstaladas);
  UI.setText('lblAvance', items.length
    ? `${enCurso} pendientes · ${finalizadas} finalizadas · ${totalCamaras} cámaras`
    : 'Sin ODS con cámaras a instalar');
  UI.setText('camOdtTotal', items.length ? UI.fmtNum(items.length) : '—');
  UI.setText('camTotalContratadas', items.length ? UI.fmtNum(totalCamaras) : '—');
  UI.setText('camTotalInstaladas', items.length ? UI.fmtNum(totalInstaladas) : '—');
  UI.setText('camTotalPendientes', items.length ? UI.fmtNum(totalPendientes) : '—');

  UI.setHTML('camProgress', items.map(({ a, total, instaladas, pct, fin }) => {
    let label;
    if (fin) label = total ? `${total}/${total} cámaras` : 'Completado';
    else if (total && instaladas !== null) label = `${instaladas}/${total} cámaras`;
    else if (total) label = `0/${total} cámaras · sin avance registrado`;
    else label = 'Sin registro de avance';
    const lugar = [a.sucursal, a.direccion, a.tecnico ? 'Técnico: ' + a.tecnico : ''].filter(Boolean).join(' · ');
    return `
    <div class="camp-item">
      <div class="camp-top">
        <span class="camp-odt">${UI.esc(a.ods)}</span>
        <span class="camp-cliente" title="${UI.esc(lugar)}">${UI.esc(a.cliente)}${a.sucursal ? ' · ' + UI.esc(a.sucursal) : ''}</span>
        ${UI.badgeEstado(fin ? 'finalizada' : 'pendiente', a.estado_cierre || (fin ? 'Finalizado' : 'Pendiente'))}
        <span class="camp-count">${label}</span>
      </div>
      <div class="camp-bar">
        <div class="camp-fill${fin ? ' done' : ''}" style="width:${pct !== null ? pct : 0}%"></div>
      </div>
    </div>`;
  }).join('') || '<div class="cam-empty">Sin ODS de venta con cámaras a instalar</div>');
}

/* ── Dashboard 2 · Calidad de instalación ────────────────────────── */
function renderInstalaciones(data) {
  const cfg = M.getConfig();
  const inst = M.calcularInstalaciones(data, DATA);
  const conCaida = inst.filter(i => i.diasACaida !== null);

  UI.setText('instTotal', inst.length ? UI.fmtNum(inst.length) : '—');
  UI.setText('instSin', inst.length ? UI.fmtNum(inst.length - conCaida.length) : '—');
  UI.setText('instCon', inst.length ? UI.fmtNum(conCaida.length) : '—');
  UI.setText('instTiempo', conCaida.length ? UI.fmtDias(M.avg(conCaida.map(i => i.diasACaida))) : '—');

  if (inst.length) {
    const f = d => Math.round(conCaida.filter(i => i.diasACaida <= d).length / inst.length * 100);
    UI.setText('instVentanas', `Falla temprana — ≤7d: ${f(7)}% · ≤15d: ${f(15)}% · ≤30d: ${f(30)}%`);
  } else {
    UI.setText('instVentanas', 'Sin ODT de tipo "Instalación" en el período: registra las instalaciones con ese tipo para activar esta sección.');
  }
  UI.setText('instReglas', `Mala: caída ≤ ${cfg.instalacion_mala_dias} días · Regular: ≤ ${cfg.instalacion_regular_dias} días · Buena: sin caída en ${cfg.instalacion_regular_dias} días`);

  UI.setHTML('tbInstalaciones', inst
    .slice()
    .sort((a, b) => (a.diasACaida === null ? 1e9 : a.diasACaida) - (b.diasACaida === null ? 1e9 : b.diasACaida))
    .map(i => `<tr>
      <td class="td-mono">${UI.esc(i.odt)}</td>
      <td class="td-main">${UI.esc(i.cliente)}</td>
      <td>${UI.fmtFecha(i.fInst)}</td>
      <td>${UI.esc(i.tecnicos || '—')}</td>
      <td>${i.primeraCaida ? UI.fmtFecha(i.primeraCaida) : '<span class="badge b-green">Sin caída</span>'}</td>
      <td>${i.diasACaida !== null ? UI.fmtDias(i.diasACaida) : '—'}</td>
      <td>${UI.badgeCalidad(i.calidad)}</td>
    </tr>`).join('') || '<tr class="tr-empty"><td colspan="7" class="td-empty">Sin ODT de tipo Instalación en el período</td></tr>');
}

/* ── 8 · Reincidencias ───────────────────────────────────────────── */
function renderReincidencias(data) {
  const alertas = M.calcularAlertas(data);
  UI.setText('lblAlertas', alertas.length ? alertas.length + ' alertas activas' : 'Sin alertas activas');

  const helpers = {
    fmtF: r => UI.fmtFecha(M.fparse(r.fecha_registro)),
    badgeE: r => UI.badgeEstado(M.estadoNorm(r), r.estado),
    uniqFn: M.uniq,
    tecnicosFn: M.tecnicosDe,
  };
  UI.setHTML('alertsGrid', alertas.length
    ? alertas.map(g => UI.alertCard(g, helpers)).join('')
    : '<div class="alerts-empty">✅ Ninguna sucursal registra 2 o más ODT en la semana actual.</div>');
}

/* ── 9 · Desconexiones ───────────────────────────────────────────── */
function renderDesconexiones(data) {
  const p = C.themePalette();
  const { desc, porCliente } = M.desconexionStats(data);
  UI.setText('lblDesconexiones', desc.length + ' ODT de tipo Desconexión en el período');

  UI.setHTML('tbDesconexiones', porCliente.slice(0, 10).map((s, i) => {
    const ultima = s.fechas.length ? new Date(Math.max.apply(null, s.fechas)) : null;
    const primera = s.fechas.length ? new Date(Math.min.apply(null, s.fechas)) : null;
    const span = primera ? Math.max(1, M.mesesEntre(primera, new Date())) : 1;
    const respFrec = Object.entries(s.resp).sort((a, b) => b[1] - a[1])[0];
    const estadosTxt = Object.entries(s.estados).map(([e, n]) => e + ': ' + n).join(' · ');
    return `<tr>
      <td class="td-dim">${i + 1}</td>
      <td class="td-main">${UI.esc(s.cliente)}</td>
      <td><span class="badge no-dot b-red">${s.total}</span></td>
      <td>${M.round1(s.total / span)}/mes</td>
      <td>${ultima ? UI.fmtFecha(ultima) : '—'}</td>
      <td>${respFrec ? UI.badgeResp(respFrec[0]) : '<span class="td-dim">—</span>'}</td>
      <td class="td-dim">${UI.esc(estadosTxt)}</td>
      <td>${s.reinc ? `<span class="badge no-dot b-red">${s.reinc}</span>` : '0'}</td>
    </tr>`;
  }).join('') || '<tr class="tr-empty"><td colspan="8" class="td-empty">Sin desconexiones en el período</td></tr>');

  const meses = M.mesesDe(desc);
  C.areaChart('chDesconexiones', meses.map(UI.fmtMes),
    [{ name: 'Desconexiones', data: M.serieMensual(desc, meses) }],
    { colors: [p.red] });
}

/* ── 10 · Reportes: ODT antiguas + export ────────────────────────── */
function renderReportes(data) {
  const umbral = umbralAntiguas();
  const rows = data
    .filter(r => M.isAbierta(r) && (M.diasAbierta(r) || 0) >= umbral)
    .sort((a, b) => (M.diasAbierta(b) || 0) - (M.diasAbierta(a) || 0));
  UI.setText('lblAntiguas', rows.length + ' ODT abiertas hace ' + umbral + ' días o más');
  UI.setHTML('tbAntiguas', rows.map(r => {
    const dias = Math.floor(M.diasAbierta(r) || 0);
    return `<tr>
      <td class="td-mono">${UI.esc(r.odt)}</td>
      <td class="td-main">${UI.esc(r.cliente)}</td>
      <td class="td-dim">${UI.esc(r.direccion || '—')}</td>
      <td>${UI.fmtFecha(M.fparse(r.fecha_registro))}</td>
      <td><span class="badge no-dot ${dias > umbral * 2 ? 'b-red' : 'b-amber'}">${dias} d</span></td>
      <td>${UI.badgeEstado(M.estadoNorm(r), r.estado)}</td>
      <td>${UI.esc(r.tecnicos || '—')}</td>
      <td>${UI.esc(r.problema || '—')}</td>
    </tr>`;
  }).join('') || `<tr class="tr-empty"><td colspan="8" class="td-empty">Sin ODT abiertas con ${umbral} días o más</td></tr>`);
}

/* ── Export ──────────────────────────────────────────────────────── */
function exportar() {
  const data = filtered();
  const header = ['ODT', 'Cliente/Sucursal', 'Direccion', 'Tipo', 'Estado', 'Fecha creacion', 'Fecha cierre', 'Dias ejecucion', 'Tecnicos', 'Acompanante', 'Responsable falla', 'Causa', 'Accion', 'Resultado', 'Requiere seguimiento', 'Observacion final', 'SLA', 'Reincidente', 'Dias abiertos/cierre'];
  const rows = data.map(r => {
    const st = M.slaStatus(r, slaObjetivo());
    const d = M.isFinal(r) ? M.cierreDias(r) : M.diasAbierta(r);
    return [
      r.odt, r.cliente, r.direccion, r.problema, r.estado,
      r.fecha_registro, r.fecha_cierre, r.dias_ejecucion,
      r.tecnicos, r.acompanante, r.responsable_cierre, r.causa_cierre,
      r.accion_cierre, r.resultado_cierre, r.requiere_seguimiento ? 'Si' : 'No',
      r.observacion_final,
      st === 'cumplida' ? 'Cumplido' : (st === 'incumplida' ? 'Incumplido' : (st === 'en_plazo' ? 'En plazo' : '')),
      r._reincidente ? 'Si' : 'No',
      d !== null ? M.round1(d) : '',
    ];
  });
  UI.downloadCSV('odt_servicio_tecnico_' + new Date().toISOString().slice(0, 10) + '.csv', header, rows);
}

/* ── Arranque ────────────────────────────────────────────────────── */
bootstrap();
