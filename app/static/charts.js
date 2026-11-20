(function () {
  const fa = new Intl.NumberFormat('fa-IR');
  const short = v => { const a = Math.abs(v), s = v < 0 ? '−' : '';
    if (a >= 1e9) return s + fa.format(+(a / 1e9).toFixed(1)) + ' میلیارد';
    if (a >= 1e6) return s + fa.format(+(a / 1e6).toFixed(1)) + ' میلیون';
    return s + fa.format(a); };
  const full = v => fa.format(v) + ' تومان';
  const C = { inc: '#1f7a4d', exp: '#7a3b8f', net: '#0e5c8a', ink: '#17211e', grid: '#d6ddd9' };
  const font = 'Vazirmatn, Tahoma, sans-serif';
  const charts = {};
  const get = id => charts[id] || (charts[id] = echarts.init(document.getElementById(id), null, { renderer: 'svg' }));
  const base = { textStyle: { fontFamily: font, color: C.ink }, grid: { left: 16, right: 16, top: 40, bottom: 16, containLabel: true },
    tooltip: { trigger: 'axis', textStyle: { fontFamily: font }, valueFormatter: full } };
  const set = (k, v) => { const el = document.getElementById(k); if (el) el.textContent = v; };

  function bars(id, rows, color) {
    get(id).setOption({ ...base, grid: { left: 24, right: 170, top: 16, bottom: 24, containLabel: false },
      tooltip: { ...base.tooltip, trigger: 'axis', axisPointer: { type: 'shadow' } },
      xAxis: { type: 'value', inverse: true, axisLabel: { formatter: short }, splitLine: { lineStyle: { color: C.grid } } },
      yAxis: { type: 'category', position: 'right', inverse: true, data: rows.map(r => r.name), axisLabel: { width: 155, overflow: 'truncate', align: 'left', margin: 10 } },
      series: [{ type: 'bar', data: rows.map(r => r.value), itemStyle: { color }, barMaxWidth: 22 }] }, true);
  }
  async function load() {
    const qs = new URLSearchParams(new FormData(document.getElementById('cf'))).toString();
    const d = await (await fetch('/api/stats/dashboard?' + qs)).json();
    set('k-income', full(d.cards.income)); set('k-expense', full(d.cards.expense)); set('k-profit', full(d.cards.profit));
    set('k-count', fa.format(d.cards.count)); set('k-vat', fa.format(d.vat.paid) + ' / ' + fa.format(d.vat.received));
    set('pending', d.pending ? `${fa.format(d.pending)} فاکتور منتظر بازبینی است و در نمودارها حساب نشده.` : 'فاکتور منتظر بازبینی‌ای وجود ندارد.');
    get('c-monthly').setOption({ ...base, legend: { top: 0, left: 'center', itemGap: 28, textStyle: { fontFamily: font } },
      xAxis: { type: 'category', inverse: true, data: d.monthly.labels, axisLabel: { interval: 0, rotate: d.monthly.labels.length > 6 ? 30 : 0 } },
      yAxis: { type: 'value', position: 'right', axisLabel: { formatter: short }, splitLine: { lineStyle: { color: C.grid } } },
      series: [{ name: 'درآمد', type: 'bar', data: d.monthly.income, itemStyle: { color: C.inc }, barMaxWidth: 26 },
               { name: 'هزینه', type: 'bar', data: d.monthly.expense, itemStyle: { color: C.exp }, barMaxWidth: 26 },
               { name: 'سود/زیان خالص', type: 'line', data: d.monthly.profit, symbolSize: 7, lineStyle: { width: 3, color: C.net }, itemStyle: { color: C.net } }] }, true);
    get('c-cat').setOption({ ...base, tooltip: { trigger: 'item', textStyle: { fontFamily: font }, valueFormatter: full }, legend: { bottom: 0, itemGap: 18, textStyle: { fontFamily: font } },
      series: [{ type: 'pie', radius: ['42%', '68%'], center: ['50%', '45%'], label: { formatter: '{b}', fontFamily: font },
        data: d.categories.map(c => ({ name: c.name, value: c.value })), color: ['#0e5c8a', '#7a3b8f', '#b07a14', '#1f7a4d', '#a3405a', '#64748b'] }] }, true);
    bars('c-sellers', d.top_sellers, C.exp); bars('c-buyers', d.top_buyers, C.inc);
  }
  const f = document.getElementById('cf');
  f.addEventListener('change', load); f.addEventListener('reset', () => setTimeout(load));
  f.addEventListener('keyup', e => { if (e.target.name && e.target.name.startsWith('date')) load(); });
  window.addEventListener('resize', () => Object.values(charts).forEach(c => c.resize()));
  // measure text only after the web font is ready, otherwise labels are clipped / legends overlap
  (document.fonts && document.fonts.load ? document.fonts.load('14px Vazirmatn') : Promise.resolve()).then(load, load);
})();
