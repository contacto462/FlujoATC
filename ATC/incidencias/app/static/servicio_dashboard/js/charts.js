/* charts.js — Wrappers de ApexCharts con tema corporativo.
   Todos los colores se leen de las variables CSS para que los gráficos
   sigan el modo claro/oscuro automáticamente. */

const registry = {};

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function themePalette() {
  return {
    text2: cssVar('--text-2') || '#475467',
    text3: cssVar('--text-3') || '#98a2b3',
    border: cssVar('--border') || '#e5e8f0',
    brand: cssVar('--brand') || '#1d3a8a',
    accent: cssVar('--accent') || '#ea580c',
    green: cssVar('--green') || '#079455',
    amber: cssVar('--amber') || '#b54708',
    red: cssVar('--red') || '#d92d20',
    blue: cssVar('--blue') || '#1570ef',
    purple: cssVar('--purple') || '#6938ef',
    teal: cssVar('--teal') || '#0e9384',
    slate: cssVar('--slate') || '#64748b',
    isDark: document.documentElement.getAttribute('data-theme') === 'dark',
  };
}

export function respColors() {
  const p = themePalette();
  return { 'ATC': p.red, 'Cliente': p.blue, 'Proveedor Externo': p.purple, 'Internet': p.teal, 'Otro': p.slate };
}

function baseOptions() {
  const p = themePalette();
  return {
    chart: {
      fontFamily: 'Inter, "Segoe UI", system-ui, sans-serif',
      foreColor: p.text3,
      toolbar: { show: false },
      animations: { speed: 450, animateGradually: { enabled: false } },
      parentHeightOffset: 0,
      background: 'transparent',
    },
    grid: { borderColor: p.border, strokeDashArray: 4, padding: { left: 8, right: 8 } },
    tooltip: { theme: p.isDark ? 'dark' : 'light' },
    dataLabels: { enabled: false },
    legend: {
      position: 'top',
      horizontalAlign: 'left',
      fontSize: '12px',
      fontWeight: 600,
      markers: { size: 5, shape: 'circle', offsetX: -2 },
      itemMargin: { horizontal: 10 },
    },
    states: { hover: { filter: { type: 'lighten', value: 0.04 } } },
  };
}

function deepMerge(target, src) {
  Object.keys(src).forEach(k => {
    if (src[k] && typeof src[k] === 'object' && !Array.isArray(src[k]) && target[k] && typeof target[k] === 'object' && !Array.isArray(target[k])) {
      deepMerge(target[k], src[k]);
    } else {
      target[k] = src[k];
    }
  });
  return target;
}

/* Renderiza (o re-renderiza) un gráfico en el host indicado.
   Si empty=true muestra placeholder "sin datos". */
export function renderChart(hostId, options, empty) {
  const host = document.getElementById(hostId);
  if (!host) return;
  if (registry[hostId]) { registry[hostId].destroy(); delete registry[hostId]; }
  host.innerHTML = '';
  if (empty) {
    host.innerHTML = '<div class="chart-empty">Sin datos en el período seleccionado</div>';
    return;
  }
  const merged = deepMerge(baseOptions(), options);
  const chart = new ApexCharts(host, merged);
  chart.render();
  registry[hostId] = chart;
}

export function destroyAll() {
  Object.keys(registry).forEach(id => { registry[id].destroy(); delete registry[id]; });
}

/* ── Presets ─────────────────────────────────────────────────────── */

export function areaChart(hostId, categories, series, opts) {
  const o = opts || {};
  renderChart(hostId, {
    chart: { type: 'area', height: o.height || 265, stacked: false },
    series,
    xaxis: {
      categories,
      labels: { style: { fontSize: '11px' } },
      axisBorder: { show: false }, axisTicks: { show: false },
    },
    yaxis: {
      labels: { style: { fontSize: '11px' }, formatter: v => (o.suffix ? Math.round(v) + o.suffix : Math.round(v)) },
      max: o.max,
    },
    stroke: { curve: 'smooth', width: 2.4 },
    fill: {
      type: 'gradient',
      gradient: { shadeIntensity: 0.8, opacityFrom: 0.32, opacityTo: 0.02, stops: [0, 95] },
    },
    colors: o.colors,
    markers: { size: 0, hover: { size: 5 } },
    annotations: o.annotations,
  }, categories.length === 0);
}

export function donutChart(hostId, labels, values, colors, opts) {
  const p = themePalette();
  const o = opts || {};
  renderChart(hostId, {
    chart: { type: 'donut', height: o.height || 265 },
    series: values,
    labels,
    colors,
    legend: { position: 'right', fontSize: '12px' },
    stroke: { width: 2, colors: [p.isDark ? '#121a2c' : '#ffffff'] },
    plotOptions: {
      pie: {
        donut: {
          size: '72%',
          labels: {
            show: true,
            name: { fontSize: '12px', offsetY: 18, color: p.text3 },
            value: { fontSize: '26px', fontWeight: 800, offsetY: -14 },
            total: { show: true, label: o.totalLabel || 'Total', fontSize: '11px', fontWeight: 600, color: p.text3 },
          },
        },
      },
    },
  }, values.reduce((s, v) => s + v, 0) === 0);
}

export function hbarChart(hostId, categories, values, color, opts) {
  const o = opts || {};
  renderChart(hostId, {
    chart: { type: 'bar', height: o.height || Math.max(180, categories.length * 36 + 50) },
    series: [{ name: o.name || 'Valor', data: values }],
    xaxis: { categories, labels: { style: { fontSize: '11px' } } },
    yaxis: { labels: { style: { fontSize: '11.5px' }, maxWidth: 220 } },
    plotOptions: { bar: { horizontal: true, borderRadius: 5, barHeight: '55%', distributed: !!o.distributed } },
    colors: Array.isArray(color) ? color : [color],
    legend: { show: false },
    tooltip: { y: { formatter: v => (o.suffix ? v + o.suffix : v) } },
  }, categories.length === 0);
}

export function stackedColumns(hostId, categories, series, colors, opts) {
  const o = opts || {};
  renderChart(hostId, {
    chart: { type: 'bar', height: o.height || 265, stacked: true },
    series,
    xaxis: { categories, labels: { style: { fontSize: '11px' } }, axisBorder: { show: false }, axisTicks: { show: false } },
    yaxis: { labels: { style: { fontSize: '11px' }, formatter: v => Math.round(v) } },
    plotOptions: { bar: { borderRadius: 4, columnWidth: '52%', borderRadiusApplication: 'end', borderRadiusWhenStacked: 'last' } },
    colors,
  }, categories.length === 0);
}

export function radialSla(hostId, pctValue, label) {
  const p = themePalette();
  const ok = pctValue !== null;
  const color = !ok ? p.slate : (pctValue >= 80 ? p.green : (pctValue >= 60 ? p.amber : p.red));
  renderChart(hostId, {
    chart: { type: 'radialBar', height: 265 },
    series: [ok ? pctValue : 0],
    labels: [label || 'Cumplimiento'],
    colors: [color],
    plotOptions: {
      radialBar: {
        hollow: { size: '64%' },
        track: { background: p.border, strokeWidth: '92%' },
        dataLabels: {
          name: { fontSize: '12px', color: p.text3, offsetY: 26 },
          value: {
            fontSize: '30px', fontWeight: 800, offsetY: -12,
            formatter: () => (ok ? pctValue + '%' : '—'),
          },
        },
      },
    },
    stroke: { lineCap: 'round' },
  }, false);
}
