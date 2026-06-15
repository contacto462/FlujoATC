/* metrics.js — Cálculo de indicadores del Dashboard Servicio Técnico.
   Funciones puras sobre los registros de ODT; sin DOM. */

export const RESPONSABLES = ['ATC', 'Cliente', 'Proveedor Externo', 'Internet', 'Otro'];

let CFG = {
  sla_dias: 3,
  odt_antigua_dias: 14,
  reincidencia_ventana_dias: 7,
  instalacion_mala_dias: 15,
  instalacion_regular_dias: 30,
};

export function setConfig(cfg) { CFG = Object.assign({}, CFG, cfg); }
export function getConfig() { return CFG; }

/* ── Básicos ─────────────────────────────────────────────────────── */
export function fparse(s) { return s ? new Date(s) : null; }

export function estadoNorm(r) {
  const e = (r.estado || '').toLowerCase();
  if (e.includes('termin') || e.includes('final') || e.includes('cerr')) return 'finalizada';
  if (e.includes('proceso')) return 'proceso';
  if (e.includes('pend')) return 'pendiente';
  return 'otro';
}
export const isFinal = r => estadoNorm(r) === 'finalizada';
export const isPend = r => estadoNorm(r) === 'pendiente';
export const isProc = r => estadoNorm(r) === 'proceso';
export const isAbierta = r => !isFinal(r);

export function cierreDias(r) {
  const fr = fparse(r.fecha_registro), fc = fparse(r.fecha_cierre);
  if (fr && fc && fc >= fr) return (fc - fr) / 864e5;
  if (typeof r.dias_ejecucion === 'number' && r.dias_ejecucion >= 0) return r.dias_ejecucion;
  return null;
}
export function diasAbierta(r) {
  const fr = fparse(r.fecha_registro);
  return fr ? (Date.now() - fr.getTime()) / 864e5 : null;
}

/* 'cumplida' | 'incumplida' | 'en_plazo' | null */
export function slaStatus(r, objetivo) {
  const dias = objetivo || CFG.sla_dias;
  if (isFinal(r)) {
    const d = cierreDias(r);
    if (d === null) return null;
    return d <= dias ? 'cumplida' : 'incumplida';
  }
  const d = diasAbierta(r);
  if (d === null) return null;
  return d > dias ? 'incumplida' : 'en_plazo';
}

export function tecnicosDe(r) {
  const out = [];
  (r.tecnicos || '').split(/[,;]/).forEach(t => { const n = t.trim(); if (n) out.push(n); });
  (r.acompanante || '').split(/[,;]/).forEach(t => { const n = t.trim(); if (n && out.indexOf(n) === -1) out.push(n); });
  return out;
}

export function esInstalacion(r) { return (r.problema || '').toLowerCase().includes('instala'); }
export function esDesconexion(r) { return (r.problema || '').toLowerCase().includes('desconex'); }
export function esFalla(r) {
  return !esInstalacion(r) && !(r.problema || '').toLowerCase().includes('mantencion preventiva');
}

/* ── Reincidencias: marca r._reincidente en cada registro ────────── */
export function marcarReincidencias(registros) {
  const ventanaMs = CFG.reincidencia_ventana_dias * 864e5;
  const porCliente = {};
  registros.forEach(r => {
    const c = (r.cliente || '').trim();
    if (!c) { r._reincidente = false; return; }
    (porCliente[c] = porCliente[c] || []).push(r);
  });
  Object.values(porCliente).forEach(list => {
    list.sort((a, b) => new Date(a.fecha_registro || 0) - new Date(b.fecha_registro || 0));
    list.forEach((r, i) => {
      r._reincidente = false;
      const fr = fparse(r.fecha_registro);
      if (!fr || i === 0) return;
      const prev = fparse(list[i - 1].fecha_registro);
      if (prev && (fr - prev) <= ventanaMs) r._reincidente = true;
    });
  });
}

/* ── Agregadores ─────────────────────────────────────────────────── */
export function countBy(arr, fn) {
  const map = {};
  arr.forEach(item => { const k = fn(item); map[k] = (map[k] || 0) + 1; });
  return Object.entries(map).sort((a, b) => b[1] - a[1]).map(([label, count]) => ({ label, count }));
}

export function avg(arr) { return arr.length ? arr.reduce((s, x) => s + x, 0) / arr.length : null; }
export function round1(x) { return Math.round(x * 10) / 10; }
export function uniq(arr) { return [...new Set(arr.filter(Boolean))]; }
export function pct(n, total) { return total ? Math.round(n / total * 100) : null; }

export function grupoPromedioCierre(arr, fn) {
  const map = {};
  arr.forEach(r => {
    const d = cierreDias(r);
    if (d === null) return;
    const k = fn(r);
    (map[k] = map[k] || []).push(d);
  });
  return Object.entries(map)
    .map(([label, ds]) => ({ label, avg: avg(ds), n: ds.length }))
    .sort((a, b) => b.avg - a.avg);
}

/* ── Series mensuales ────────────────────────────────────────────── */
export function monthKey(s) {
  const d = fparse(s);
  if (!d) return null;
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
}
export function mesesDe(arr, fechaFn) {
  const keys = uniq(arr.map(r => monthKey(fechaFn ? fechaFn(r) : r.fecha_registro)));
  return keys.sort().slice(-12);
}
export function serieMensual(arr, meses, predicado, fechaFn) {
  return meses.map(m => arr.filter(r => monthKey(fechaFn ? fechaFn(r) : r.fecha_registro) === m && (!predicado || predicado(r))).length);
}

export function isoWeekKey(d) {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((t - yearStart) / 864e5 + 1) / 7);
  return t.getUTCFullYear() + '-W' + String(week).padStart(2, '0');
}

/* ── KPIs de un conjunto de registros ────────────────────────────── */
export function resumen(data, slaObjetivo) {
  const total = data.length;
  const fin = data.filter(isFinal).length;
  const pend = data.filter(isPend).length;
  const proc = data.filter(isProc).length;
  const reinc = data.filter(r => r._reincidente).length;
  const tiempos = data.filter(isFinal).map(cierreDias).filter(d => d !== null);
  const slaEval = data.map(r => slaStatus(r, slaObjetivo)).filter(s => s === 'cumplida' || s === 'incumplida');
  const slaOk = slaEval.filter(s => s === 'cumplida').length;
  return {
    total, fin, pend, proc, reinc,
    activas: pend + proc,
    tasaResolucion: pct(fin, total),
    tiempoProm: avg(tiempos),
    tiempoPromN: tiempos.length,
    slaPct: slaEval.length ? Math.round(slaOk / slaEval.length * 100) : null,
    slaOk,
    slaEvaluables: slaEval.length,
    slaFuera: slaEval.length - slaOk,
  };
}

/* Variación % entre dos valores (para flechas de tendencia) */
export function delta(actual, anterior) {
  if (anterior === null || actual === null || anterior === undefined || actual === undefined) return null;
  if (anterior === 0) return actual > 0 ? 100 : 0;
  return Math.round((actual - anterior) / anterior * 100);
}

/* ── Estadísticas por cliente ────────────────────────────────────── */
export function clienteStats(data, slaObjetivo) {
  const map = {};
  data.forEach(r => {
    const c = (r.cliente || '').trim() || 'Sin cliente';
    const s = map[c] = map[c] || {
      cliente: c, total: 0, fin: 0, pend: 0, proc: 0, tiempos: [],
      atc: 0, cli: 0, diag: 0, reinc: 0, slaOk: 0, slaEval: 0, desconex: 0, instMala: 0,
    };
    s.total++;
    if (isFinal(r)) { s.fin++; const d = cierreDias(r); if (d !== null) s.tiempos.push(d); }
    if (isPend(r)) s.pend++;
    if (isProc(r)) s.proc++;
    if (r._reincidente) s.reinc++;
    if (esDesconexion(r)) s.desconex++;
    if (isFinal(r) && r.responsable_cierre) {
      s.diag++;
      if (r.responsable_cierre === 'ATC') s.atc++;
      if (r.responsable_cierre === 'Cliente') s.cli++;
    }
    const st = slaStatus(r, slaObjetivo);
    if (st === 'cumplida') { s.slaOk++; s.slaEval++; }
    else if (st === 'incumplida') s.slaEval++;
  });
  return Object.values(map).sort((a, b) => b.total - a.total);
}

/* Criticidad automática: factores de riesgo acumulados */
export function criticidad(s, umbralVolumen) {
  let factores = 0;
  if (s.total >= umbralVolumen && s.total >= 3) factores++;
  if (s.reinc >= 2) factores++;
  if (s.slaEval >= 2 && (s.slaOk / s.slaEval) < 0.7) factores++;
  if (s.desconex >= 3) factores++;
  if (s.instMala >= 1) factores++;
  if (factores >= 3) return 'Crítico';
  if (factores === 2) return 'Medio';
  return 'Normal';
}

export function umbralVolumen(stats) {
  const totales = stats.map(s => s.total).sort((a, b) => b - a);
  if (!totales.length) return 3;
  return Math.max(3, totales[Math.floor(totales.length * 0.2)] || 3);
}

/* ── Instalaciones: primera caída posterior ──────────────────────── */
export function calcularInstalaciones(data, universo) {
  const all = universo || data;
  return data.filter(esInstalacion).map(r => {
    const fInst = fparse(r.fecha_cierre) || fparse(r.fecha_registro);
    let primeraCaida = null;
    if (fInst) {
      const fallas = all
        .filter(s => s !== r && (s.cliente || '').trim() === (r.cliente || '').trim() && esFalla(s))
        .map(s => fparse(s.fecha_registro))
        .filter(f => f && f > fInst)
        .sort((a, b) => a - b);
      primeraCaida = fallas.length ? fallas[0] : null;
    }
    const diasACaida = (fInst && primeraCaida) ? (primeraCaida - fInst) / 864e5 : null;
    let calidad = 'Buena';
    if (diasACaida !== null) {
      if (diasACaida <= CFG.instalacion_mala_dias) calidad = 'Mala';
      else if (diasACaida <= CFG.instalacion_regular_dias) calidad = 'Regular';
    }
    return {
      odt: r.odt,
      cliente: (r.cliente || '').trim(),
      fInst,
      tecnicos: tecnicosDe(r).join(', '),
      primeraCaida,
      diasACaida,
      calidad,
    };
  });
}

/* ── Alertas de reincidencia por semana ISO ──────────────────────── */
export function calcularAlertas(data) {
  const grupos = {};
  data.forEach(r => {
    const f = fparse(r.fecha_registro);
    const c = (r.cliente || '').trim();
    if (!f || !c) return;
    const wk = isoWeekKey(f);
    const key = c + '||' + wk;
    (grupos[key] = grupos[key] || { cliente: c, semana: wk, odts: [] }).odts.push(r);
  });
  return Object.values(grupos)
    .filter(g => g.odts.length >= 2)
    .map(g => {
      const n = g.odts.length;
      g.nivel = n >= 4 ? 'Rojo' : (n >= 3 ? 'Naranja' : 'Amarillo');
      g.orden = Math.max(...g.odts.map(r => +(fparse(r.fecha_registro) || 0)));
      return g;
    })
    .sort((a, b) => (b.odts.length - a.odts.length) || (b.orden - a.orden));
}

/* ── Productividad por técnico ───────────────────────────────────── */
export function tecnicoStats(data, slaObjetivo) {
  const map = {};
  data.forEach(r => {
    tecnicosDe(r).forEach(t => {
      const s = map[t] = map[t] || { tecnico: t, total: 0, fin: 0, tiempos: [], slaOk: 0, slaEval: 0, reinc: 0, fuera: 0 };
      s.total++;
      if (isFinal(r)) { s.fin++; const d = cierreDias(r); if (d !== null) s.tiempos.push(d); }
      if (r._reincidente) s.reinc++;
      const st = slaStatus(r, slaObjetivo);
      if (st === 'cumplida') { s.slaOk++; s.slaEval++; }
      else if (st === 'incumplida') { s.slaEval++; s.fuera++; }
    });
  });
  return Object.values(map).sort((a, b) => b.fin - a.fin || b.total - a.total);
}

/* ── Desconexiones por cliente ───────────────────────────────────── */
export function desconexionStats(data) {
  const desc = data.filter(esDesconexion);
  const map = {};
  desc.forEach(r => {
    const c = (r.cliente || '').trim() || 'Sin cliente';
    const s = map[c] = map[c] || { cliente: c, total: 0, fechas: [], resp: {}, estados: {}, reinc: 0 };
    s.total++;
    const f = fparse(r.fecha_registro); if (f) s.fechas.push(f);
    if (r.responsable_cierre) s.resp[r.responsable_cierre] = (s.resp[r.responsable_cierre] || 0) + 1;
    const est = r.estado || '—';
    s.estados[est] = (s.estados[est] || 0) + 1;
    if (r._reincidente) s.reinc++;
  });
  return { desc, porCliente: Object.values(map).sort((a, b) => b.total - a.total) };
}

export function mesesEntre(a, b) {
  return (b.getFullYear() - a.getFullYear()) * 12 + (b.getMonth() - a.getMonth()) + 1;
}
