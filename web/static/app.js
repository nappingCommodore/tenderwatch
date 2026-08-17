/* Shared front-end helpers: ECharts dark theme + initializers driven by
   JSON embedded in the page (<script type="application/json" id="...">). */

const CSS = getComputedStyle(document.documentElement);
const C = {
  accent: CSS.getPropertyValue('--accent').trim() || '#f0553d',
  text: CSS.getPropertyValue('--text').trim() || '#e7e9ec',
  muted: CSS.getPropertyValue('--muted').trim() || '#97a1ac',
  border: CSS.getPropertyValue('--border').trim() || '#232a33',
  panel: CSS.getPropertyValue('--panel').trim() || '#14181e',
  good: '#46b98a', warn: '#e0a53d', info: '#5aa9e6',
};

function readJSON(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  try { return JSON.parse(el.textContent); } catch (e) { return null; }
}

const baseGrid = { left: 8, right: 18, top: 12, bottom: 8, containLabel: true };
const axisStyle = {
  axisLine: { lineStyle: { color: C.border } },
  axisTick: { show: false },
  axisLabel: { color: C.muted },
  splitLine: { lineStyle: { color: 'rgba(255,255,255,0.04)' } },
};
function tooltip(extra) {
  return Object.assign({
    backgroundColor: C.panel, borderColor: C.border, textStyle: { color: C.text },
    confine: true,
  }, extra || {});
}
function mount(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  const ch = echarts.init(el, null, { renderer: 'canvas' });
  window.addEventListener('resize', () => ch.resize());
  return ch;
}

/* Horizontal ranked bar. data = {categories:[], values:[], fmt?} */
function hbar(id, data, opts) {
  const ch = mount(id); if (!ch || !data) return;
  opts = opts || {};
  ch.setOption({
    grid: baseGrid,
    tooltip: tooltip({ trigger: 'axis', axisPointer: { type: 'shadow' },
      valueFormatter: opts.valueFormatter }),
    xAxis: Object.assign({ type: 'value' }, axisStyle),
    yAxis: Object.assign({ type: 'category', inverse: true, data: data.categories }, axisStyle,
      { axisLabel: { color: C.muted, width: opts.labelWidth || 180, overflow: 'truncate' } }),
    series: [{
      type: 'bar', data: data.values, barWidth: '62%',
      itemStyle: { color: opts.color || C.accent, borderRadius: [0, 3, 3, 0] },
    }],
  });
}

/* Vertical time series bar. data = {categories:[], values:[], mark?:[]} */
function tbar(id, data, opts) {
  const ch = mount(id); if (!ch || !data) return;
  opts = opts || {};
  const colors = data.categories.map((c, i) =>
    (data.mark && data.mark[i]) ? C.accent : C.info);
  ch.setOption({
    grid: baseGrid,
    tooltip: tooltip({ trigger: 'axis', valueFormatter: opts.valueFormatter }),
    xAxis: Object.assign({ type: 'category', data: data.categories }, axisStyle,
      { axisLabel: { color: C.muted, rotate: opts.rotate || 0 } }),
    yAxis: Object.assign({ type: 'value', name: opts.yName || '' }, axisStyle),
    series: [{ type: 'bar', data: data.values.map((v, i) => ({ value: v, itemStyle: { color: colors[i] } })) }],
  });
}

/* Donut. data = [{name, value}] */
function donut(id, data, opts) {
  const ch = mount(id); if (!ch || !data) return;
  opts = opts || {};
  const palette = [C.accent, C.info, C.warn, C.good, '#9b7ede', '#e37fb0', '#6bbec9'];
  ch.setOption({
    tooltip: tooltip({ trigger: 'item' }),
    legend: { type: 'scroll', bottom: 0, textStyle: { color: C.muted }, itemWidth: 10, itemHeight: 10 },
    series: [{
      type: 'pie', radius: ['52%', '74%'], center: ['50%', '44%'], data,
      itemStyle: { borderColor: C.panel, borderWidth: 2 },
      label: { color: C.muted }, color: palette,
    }],
  });
}

/* Force-directed graph. data = {nodes:[{id,name,val,cat}], links:[{source,target}], categories:[]} */
function graph(id, data) {
  const ch = mount(id); if (!ch || !data) return;
  ch.setOption({
    tooltip: tooltip({}),
    legend: data.categories ? [{ data: data.categories.map(c => c.name), bottom: 0, textStyle: { color: C.muted } }] : undefined,
    series: [{
      type: 'graph', layout: 'force', roam: true, draggable: true,
      categories: data.categories,
      force: { repulsion: 140, edgeLength: [40, 130], gravity: 0.08 },
      label: { show: true, position: 'right', color: C.muted, fontSize: 11, formatter: p => p.data.name },
      lineStyle: { color: 'rgba(255,255,255,0.16)', curveness: 0.05 },
      emphasis: { focus: 'adjacency', lineStyle: { color: C.accent, width: 2 } },
      data: data.nodes.map(n => ({
        id: String(n.id), name: n.name, category: n.cat,
        symbolSize: Math.max(8, Math.min(46, n.val || 10)),
        itemStyle: n.color ? { color: n.color } : undefined,
      })),
      links: data.links.map(l => ({ source: String(l.source), target: String(l.target) })),
    }],
  });
}

/* Bihar district choropleth. geo registered from embedded geojson. */
async function choropleth(id, cfg) {
  const ch = mount(id); if (!ch || !cfg) return;
  const gj = readJSON(cfg.geojsonId);
  if (!gj) return;
  echarts.registerMap('bihar', gj);
  const vals = cfg.data.map(d => d.value);
  ch.setOption({
    tooltip: tooltip({ trigger: 'item',
      formatter: p => `${p.name}<br><b>${p.value != null ? p.value.toLocaleString() : '—'}</b> ${cfg.unit || ''}` }),
    visualMap: {
      min: 0, max: Math.max(1, ...vals), left: 12, bottom: 12, calculable: true,
      inRange: { color: ['#1a1f27', '#5a2318', '#a5341f', C.accent, '#ffb199'] },
      textStyle: { color: C.muted },
    },
    series: [{
      type: 'map', map: 'bihar', roam: true,
      nameProperty: cfg.nameProperty || 'district',
      itemStyle: { borderColor: C.border, areaColor: '#141920' },
      emphasis: { itemStyle: { areaColor: C.accent }, label: { color: '#fff' } },
      label: { show: false },
      data: cfg.data,
    }],
  });
  if (cfg.linkPrefix) {
    ch.on('click', p => { if (p && p.name) location.href = cfg.linkPrefix + encodeURIComponent(p.name); });
  }
}

window.Charts = { readJSON, hbar, tbar, donut, graph, choropleth };

/* ---- toast -------------------------------------------------------------- */
function toast(msg) {
  let host = document.getElementById('toast-host');
  if (!host) { host = document.createElement('div'); host.id = 'toast-host'; document.body.appendChild(host); }
  const t = document.createElement('div');
  t.className = 'toast'; t.textContent = msg;
  host.appendChild(t);
  requestAnimationFrame(() => t.classList.add('show'));
  setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300); }, 2200);
}

/* ---- flag help popover ("?" on flag rows) ------------------------------- */
const QHelp = {
  docs: {},
  pop: null,
  close() { if (this.pop) { this.pop.remove(); this.pop = null; } },
  init() {
    this.docs = readJSON('flag-help') || {};
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.qmark');
      if (!btn) { if (!e.target.closest('.qpop')) this.close(); return; }
      e.preventDefault();
      const d = this.docs[btn.dataset.rule];
      this.close();
      if (!d) return;
      const esc = (s) => (s || '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
      const p = document.createElement('div');
      p.className = 'qpop';
      p.innerHTML = `<div class="qh">${esc(d.title)} <span class="code">${esc(btn.dataset.rule)}</span></div>`
        + `<p>${esc(d.what)}</p>`
        + `<div class="qk">How it's computed</div><p>${esc(d.how)}</p>`
        + `<div class="qk">How to verify</div><p>${esc(d.verify)}</p>`;
      document.body.appendChild(p);
      this.pop = p;
      const r = btn.getBoundingClientRect();
      let left = window.scrollX + r.left;
      left = Math.min(left, window.scrollX + document.documentElement.clientWidth - p.offsetWidth - 14);
      p.style.top = (window.scrollY + r.bottom + 6) + 'px';
      p.style.left = Math.max(12, left) + 'px';
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') this.close(); });
    window.addEventListener('resize', () => this.close());
  }
};

/* ---- add-to-case (fetch, no reload) ------------------------------------- */
const AddForms = {
  init() {
    document.addEventListener('submit', async (e) => {
      const form = e.target.closest('.add-form');
      if (!form) return;
      e.preventDefault();
      const btn = form.querySelector('button');
      const orig = btn.textContent;
      try {
        const r = await fetch(form.action, { method: 'POST', body: new FormData(form) });
        if (!r.ok) {
          const j = await r.json().catch(() => ({}));
          if (j.error === 'no_active_case' &&
              confirm('No active case yet. Open the Casebook to start or pick one?')) {
            location.href = '/casebook';
          }
          return;
        }
        const j = await r.json();
        btn.textContent = j.created ? '\u2713 Added' : '\u2713 In case';
        btn.classList.add('added');
        const chip = document.querySelector('.casechip b');
        if (chip && j.count != null) chip.textContent = j.count;
        toast((j.created ? 'Added to ' : 'Already in ') + (j.title || 'case'));
        setTimeout(() => { btn.textContent = orig; btn.classList.remove('added'); }, 1800);
      } catch (err) {
        btn.textContent = 'error'; setTimeout(() => { btn.textContent = orig; }, 1500);
      }
    });
  }
};

/* ---- active-case picker dropdown ---------------------------------------- */
const CaseMenu = {
  init() {
    const btn = document.getElementById('casebtn');
    const menu = document.getElementById('casemenu');
    if (!btn || !menu) return;
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      btn.setAttribute('aria-expanded', String(!menu.hidden));
    });
    document.addEventListener('click', (e) => {
      if (!menu.hidden && !menu.contains(e.target) && !btn.contains(e.target)) {
        menu.hidden = true; btn.setAttribute('aria-expanded', 'false');
      }
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') menu.hidden = true; });
  }
};

document.addEventListener('DOMContentLoaded', () => { QHelp.init(); AddForms.init(); CaseMenu.init(); });
