/* api.js — Capa de datos del Dashboard Servicio Técnico.
   Fuente real: GET /api/servicio/kpis-data  →  { config, registros }
   Si el endpoint falla o la URL incluye ?mock=1, se generan datos de
   demostración para visualizar el dashboard sin base de datos. */

const DEFAULT_CONFIG = {
  sla_dias: 3,
  odt_antigua_dias: 14,
  reincidencia_ventana_dias: 7,
  instalacion_mala_dias: 15,
  instalacion_regular_dias: 30,
};

export async function loadData() {
  const wantMock = new URLSearchParams(location.search).get('mock') === '1';
  if (!wantMock) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000); // 20s máximo
    try {
      const res = await fetch('/api/servicio/kpis-data', { signal: controller.signal });
      clearTimeout(timeout);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const payload = await res.json();
      const registros = Array.isArray(payload) ? payload : (payload.registros || []);
      const config = Object.assign({}, DEFAULT_CONFIG, (payload && payload.config) || {});
      const avance = (payload && payload.avance_camaras) || [];
      return { registros, config, avance, mock: false };
    } catch (err) {
      clearTimeout(timeout);
      const reason = err.name === 'AbortError' ? 'timeout 20s' : err.message;
      console.warn('[dashboard] API no disponible (' + reason + '), usando datos mock');
    }
  }
  return { registros: generateMock(), config: Object.assign({}, DEFAULT_CONFIG), avance: generateMockAvance(), mock: true };
}

/* ── Generador de datos mock ─────────────────────────────────────── */

const MOCK_CLIENTES = [
  ['MQUIN Edificio Consistorial', 'Av. Libertad 450, Quilpué'],
  ['MQUIN Cementerio Municipal', 'Camino El Belloto s/n, Quilpué'],
  ['MQUIN DIDECO', 'Anibal Pinto 634, Quilpué'],
  ['Cesfam Llay Llay', 'Agustín Edwards 59, Llay Llay'],
  ['P Lub', '12 Norte 1287, Viña del Mar'],
  ['A.fram Viña', '5 Oriente 880, Viña del Mar'],
  ['Imq Estadio V. Olímpica', 'Av. Valparaíso 1200, Quilpué'],
  ['Imq CIAM', 'Covadonga 1271, Quilpué'],
  ['MC Museo', 'Plaza de Armas 100, Casablanca'],
  ['MC Cesfam', 'Av. Portales 320, Casablanca'],
  ['Farmacia Cruz Verde Belloto', 'Av. Concón 55, Quilpué'],
  ['Supermercado El Trébol', 'Los Carrera 980, Villa Alemana'],
];

const MOCK_TIPOS = [
  ['Desconexión', 26],
  ['Problema de Visual', 14],
  ['Problema de Alarma', 11],
  ['Problema de Parlante', 7],
  ['Hora y/o Fecha Cambiada', 8],
  ['Mantencion Preventiva', 18],
  ['Configuración', 6],
  ['Instalación', 10],
];

const MOCK_TECNICOS = [
  'Bryan Rebolledo', 'Marco López', 'Ricardo Vergara',
  'Luis Bustamante',
];

const MOCK_RESPONSABLES = [
  ['ATC', 30], ['Cliente', 34], ['Internet', 16], ['Proveedor Externo', 12], ['Otro', 8],
];

const MOCK_CAUSAS = {
  'ATC': ['instalacion_deficiente', 'configuracion_incorrecta', 'mantenimiento_insuficiente'],
  'Cliente': ['manipulacion_cliente', 'problema_electrico_cliente', 'infraestructura_cliente'],
  'Internet': ['corte_internet_zona', 'intermitencia_enlace', 'falla_router_modem'],
  'Proveedor Externo': ['falla_proveedor_servicio', 'corte_programado_proveedor'],
  'Otro': ['vandalismo', 'causa_no_determinada'],
};

function mulberry32(seed) {
  let a = seed;
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function pickWeighted(rnd, pairs) {
  const total = pairs.reduce((s, p) => s + p[1], 0);
  let roll = rnd() * total;
  for (const p of pairs) { roll -= p[1]; if (roll <= 0) return p[0]; }
  return pairs[0][0];
}

export function generateMock() {
  const rnd = mulberry32(20260611);
  const out = [];
  const now = Date.now();
  const DAY = 864e5;
  const N = 340;

  for (let i = 0; i < N; i++) {
    const cli = MOCK_CLIENTES[Math.floor(rnd() * MOCK_CLIENTES.length)];
    const tipo = pickWeighted(rnd, MOCK_TIPOS);
    // Fechas distribuidas en los últimos 14 meses, con más volumen reciente
    const edadDias = Math.floor(Math.pow(rnd(), 1.45) * 420);
    const creada = now - edadDias * DAY - rnd() * DAY;

    // Estado: lo antiguo tiende a estar cerrado
    let estado = 'Terminado';
    if (edadDias < 45) {
      const r = rnd();
      estado = r < 0.42 ? 'Terminado' : (r < 0.74 ? 'Pendiente' : 'En Proceso');
    } else if (rnd() < 0.08) {
      estado = rnd() < 0.5 ? 'Pendiente' : 'En Proceso';
    }

    const tecnico = MOCK_TECNICOS[Math.floor(rnd() * MOCK_TECNICOS.length)];
    const conAcomp = rnd() < 0.3;
    const acomp = conAcomp ? MOCK_TECNICOS[Math.floor(rnd() * MOCK_TECNICOS.length)] : '';

    let fechaCierre = null;
    let diasEjec = null;
    let responsable = '';
    let causa = '';
    let resultado = '';
    if (estado === 'Terminado') {
      // Tiempo de cierre log-normal-ish: mayoría 0.5–4 días, cola larga
      const dur = Math.max(0.15, Math.pow(rnd(), 2.2) * 16 + rnd() * 2);
      fechaCierre = creada + dur * DAY;
      diasEjec = Math.round(dur);
      responsable = pickWeighted(rnd, MOCK_RESPONSABLES);
      const causas = MOCK_CAUSAS[responsable];
      causa = causas[Math.floor(rnd() * causas.length)];
      resultado = rnd() < 0.8 ? 'operativo' : (rnd() < 0.6 ? 'operativo_con_observacion' : 'requiere_seguimiento');
    }

    out.push({
      odt: 'ODT-' + String(2400 + i),
      fecha_registro: new Date(creada).toISOString(),
      fecha_cierre: fechaCierre ? new Date(fechaCierre).toISOString() : null,
      fecha_derivacion_area: null,
      fecha_derivacion_tecnico: null,
      cliente: cli[0],
      direccion: cli[1],
      puesto: '',
      problema: tipo,
      estado: estado,
      tecnicos: tecnico,
      acompanante: acomp,
      dias_ejecucion: diasEjec,
      responsable_cierre: responsable,
      causa_cierre: causa,
      accion_cierre: estado === 'Terminado' ? 'reconexion' : '',
      resultado_cierre: resultado,
      pruebas_cierre: '',
      materiales: '',
      requiere_seguimiento: resultado === 'requiere_seguimiento',
      observacion_final: '',
    });
  }

  // Ráfagas de reincidencia para activar alertas (misma semana, mismo cliente)
  const burstCli = [MOCK_CLIENTES[4], MOCK_CLIENTES[0], MOCK_CLIENTES[3]];
  burstCli.forEach((cli, b) => {
    const base = now - (2 + b) * DAY;
    const n = 2 + b; // 2, 3 y 4 ODT → amarillo, naranja, rojo
    for (let k = 0; k < n; k++) {
      out.push({
        odt: 'ODT-' + String(2900 + b * 10 + k),
        fecha_registro: new Date(base - k * 0.8 * DAY).toISOString(),
        fecha_cierre: null,
        fecha_derivacion_area: null,
        fecha_derivacion_tecnico: null,
        cliente: cli[0],
        direccion: cli[1],
        puesto: '',
        problema: k % 2 === 0 ? 'Desconexión' : 'Problema de Visual',
        estado: k === 0 ? 'En Proceso' : 'Pendiente',
        tecnicos: k === 0 ? MOCK_TECNICOS[0] : '',
        acompanante: '',
        dias_ejecucion: null,
        responsable_cierre: '',
        causa_cierre: '',
        accion_cierre: '',
        resultado_cierre: '',
        pruebas_cierre: '',
        materiales: '',
        requiere_seguimiento: false,
        observacion_final: '',
      });
    }
  });

  return out;
}

/* Mock del avance de cámaras: simula servicio_tecnico_ventas_odt + venta_ods */
export function generateMockAvance() {
  const rnd = mulberry32(20260612);
  return MOCK_CLIENTES.slice(0, 9).map((cli, i) => {
    const total = 2 + Math.floor(rnd() * 14);
    const fin = rnd() < 0.45;
    return {
      ods: 'ODS-' + String(310 + i),
      cliente: cli[0],
      sucursal: '',
      direccion: cli[1],
      camaras_total: total,
      camaras_instaladas: fin ? total : Math.floor(rnd() * total),
      finalizada: fin,
      fecha_inicio: '',
      fecha_fin: '',
      tecnico: MOCK_TECNICOS[i % MOCK_TECNICOS.length],
    };
  });
}
