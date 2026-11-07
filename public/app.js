/* 云枢 ERP 前端：原生 ES5+，零构建、零外部依赖。
 * 所有数据来自 /api/* 真实接口；?demo=1 时暴露 window.renderAt(t) 供逐帧录制。
 */
(function () {
  'use strict';
  var GITHUB = 'https://github.com/clawgeek/modern-erp-system';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };
  var DEMO = new URLSearchParams(location.search).get('demo') === '1';
  var D = parseFloat(new URLSearchParams(location.search).get('d')) || 40;
  var NOMINAL = 44;
  if (DEMO) document.body.classList.add('demo');

  function api(path, opts) {
    opts = opts || {};
    return fetch(path, {
      method: opts.method || 'GET',
      headers: { 'Content-Type': 'application/json' },
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, data: j }; }); });
  }
  function toast(msg) {
    var t = $('#toast'); t.textContent = msg; t.classList.add('on');
    if (!DEMO) setTimeout(function () { t.classList.remove('on'); }, 2600);
  }
  function wan(v) { return (v / 100.0).toFixed(0); }

  /* ---------------- 视图切换 ---------------- */
  var TITLES = { dash: '经营驾驶舱', po: '采购协同', inv: '库存台账', fin: '业财一体化', ai: 'AI Copilot', ops: '审计与监控' };
  function switchView(v) {
    $$('.view').forEach(function (el) { el.classList.toggle('on', el.id === 'view-' + v); });
    $$('.nav').forEach(function (b) { b.classList.toggle('on', b.getAttribute('data-view') === v); });
    $('#crumb').textContent = TITLES[v] || v;
    $('.content').scrollTop = 0;
    if (!DEMO) loadView(v);
  }
  $$('.nav').forEach(function (b) { b.addEventListener('click', function () { switchView(b.getAttribute('data-view')); }); });

  /* ---------------- 渲染 ---------------- */
  function renderStats(k) {
    $('#dashStats').innerHTML =
      stat('本月订单额', k.order_amount, '共 ' + k.order_count + ' 张订单') +
      stat('准时交付率', k.on_time_rate + '%', '历史订单真实聚合') +
      stat('库存周转天数', k.inventory_days + ' 天', '库存 / 日均出库') +
      stat('逾期应收', k.overdue_ar, '账龄 > 到期日') +
      stat('待复核凭证', k.voucher_drafts + ' 张', 'AI 草稿 · 人工过账');
  }
  function stat(label, value, sub) {
    return '<div class="stat"><span>' + label + '</span><b>' + esc(value) + '</b><small>' + esc(sub) + '</small></div>';
  }
  function lineChart(series) {
    var W = 620, H = 200, pad = 30, s = '';
    var vals = series.map(function (x) { return x.amount_wan; });
    var mx = Math.max.apply(null, vals) * 1.15, mn = Math.min.apply(null, vals) * 0.75;
    var x = function (i) { return pad + i * (W - pad * 2) / Math.max(1, vals.length - 1); };
    var y = function (v) { return H - pad - (v - mn) / (mx - mn) * (H - pad * 2); };
    [0.25, 0.5, 0.75, 1].forEach(function (f) {
      var gy = H - pad - f * (H - pad * 2);
      s += '<line x1="' + pad + '" y1="' + gy + '" x2="' + (W - pad) + '" y2="' + gy + '" stroke="#eef2f8"/>';
    });
    var pts = vals.map(function (v, i) { return x(i).toFixed(1) + ',' + y(v).toFixed(1); }).join(' ');
    s += '<defs><linearGradient id="lg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2563eb" stop-opacity=".26"/><stop offset="1" stop-color="#2563eb" stop-opacity="0"/></linearGradient></defs>';
    s += '<path d="M' + x(0) + ',' + (H - pad) + ' L' + pts.replace(/ /g, ' L') + ' L' + x(vals.length - 1) + ',' + (H - pad) + ' Z" fill="url(#lg)"/>';
    s += '<polyline points="' + pts + '" fill="none" stroke="#2563eb" stroke-width="2.4" stroke-linejoin="round"/>';
    vals.forEach(function (v, i) { s += '<circle cx="' + x(i).toFixed(1) + '" cy="' + y(v).toFixed(1) + '" r="2.6" fill="#fff" stroke="#2563eb" stroke-width="1.6"/>'; });
    s += '<text x="' + pad + '" y="' + (H - 7) + '" font-size="10" fill="#94a3b8">' + esc(series[0].month) + '</text>';
    s += '<text x="' + (W - pad - 40) + '" y="' + (H - 7) + '" font-size="10" fill="#94a3b8">' + esc(series[series.length - 1].month) + '</text>';
    $('#chartLine').innerHTML = s;
  }
  function barChart(regions) {
    var colors = ['#2563eb', '#0e9f8a', '#7c3aed', '#d97706'], s = '', yy = 24;
    regions.forEach(function (r, i) {
      s += '<text x="6" y="' + (yy + 13) + '" font-size="11" fill="#44536e">' + esc(r.region) + '</text>';
      s += '<rect x="58" y="' + yy + '" width="' + Math.min(230, r.amount_wan * 2.2) + '" height="16" rx="4" fill="' + colors[i % 4] + '"/>';
      s += '<text x="' + (64 + Math.min(230, r.amount_wan * 2.2)) + '" y="' + (yy + 13) + '" font-size="10" fill="#44536e">' + wan(r.amount_wan * 100) + ' 万</text>';
      yy += 42;
    });
    $('#chartBar').innerHTML = s;
  }
  function renderDashboard(d) {
    renderStats(d.kpi); lineChart(d.series); barChart(d.regions);
    $('#alerts').innerHTML = d.alerts.map(function (a) {
      return '<div class="alert"><b>' + (a.level === 'WARN' ? '⚠' : 'ⓘ') + '</b> ' + esc(a.text) + '</div>';
    }).join('') || '<div class="alert">当前无异常</div>';
  }
  function renderOrders(list) {
    var h = '<thead><tr><th>订单号</th><th>供应商</th><th>物料</th><th>数量</th><th>金额</th><th>状态</th><th>操作</th></tr></thead><tbody>';
    list.forEach(function (o) {
      var badge = o.status === 'RECEIVED' ? 'g' : (o.status === 'CONFIRMED' ? '' : 'a');
      h += '<tr><td><b>' + esc(o.id) + '</b></td><td>' + esc(o.supplier_name) + '</td><td>' + esc(o.material_name) +
        '</td><td>' + o.qty + '</td><td>¥' + (o.amount_cents / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) +
        '</td><td><span class="badge ' + badge + '">' + esc(o.status) + '</span></td><td>' +
        (o.status === 'CONFIRMED' || o.status === 'DRAFT'
          ? '<button class="btn sm" data-receive="' + esc(o.id) + '">收货入库</button>' : '—') + '</td></tr>';
    });
    $('#orderTable').innerHTML = h + '</tbody>';
    $$('[data-receive]').forEach(function (b) { b.addEventListener('click', function () { receive(b.getAttribute('data-receive')); }); });
  }
  function renderInventory(list) {
    var h = '<thead><tr><th>SKU</th><th>物料</th><th>类别</th><th>可用库存</th><th>安全水位</th><th>单价</th><th>状态</th></tr></thead><tbody>';
    list.forEach(function (r) {
      h += '<tr><td><b>' + esc(r.sku) + '</b></td><td>' + esc(r.name) + '</td><td>' + esc(r.category) +
        '</td><td><b>' + r.qty + '</b> ' + esc(r.unit) + '</td><td>' + r.safety_stock + '</td><td>¥' +
        (r.price_cents / 100).toFixed(2) + '</td><td>' +
        (r.below_safety ? '<span class="badge r">低于安全水位</span>' : '<span class="badge g">正常</span>') + '</td></tr>';
    });
    $('#invTable').innerHTML = h + '</tbody>';
  }
  function renderVouchers(list) {
    var h = '<thead><tr><th>凭证号</th><th>摘要</th><th>金额</th><th>状态</th><th>AI 置信度</th><th>操作</th></tr></thead><tbody>';
    list.forEach(function (v) {
      h += '<tr><td><b>' + esc(v.no) + '</b></td><td>' + esc(v.memo) + '</td><td>¥' +
        (v.total_cents / 100).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) +
        '</td><td><span class="badge ' + (v.status === 'POSTED' ? 'g' : 'a') + '">' + esc(v.status) + '</span></td><td>' +
        (v.ai_confidence ? v.ai_confidence : '—') + '</td><td>' +
        (v.status === 'DRAFT' ? '<button class="btn sm violet" data-approve="' + esc(v.no) + '">复核过账</button>' : '已过账') +
        '</td></tr>';
      h += '<tr><td colspan="6" style="background:#fafcff;padding:5px 12px;font-size:10.5px;color:#74839c">分录：' +
        v.lines.map(function (l) {
          return esc(l.account) + (l.cost_center ? '(' + esc(l.cost_center) + ')' : '') +
            ' 借 ¥' + (l.debit_cents / 100).toFixed(2) + ' / 贷 ¥' + (l.credit_cents / 100).toFixed(2);
        }).join('　｜　') + '</td></tr>';
    });
    $('#voucherTable').innerHTML = h + '</tbody>';
    $$('[data-approve]').forEach(function (b) { b.addEventListener('click', function () { approve(b.getAttribute('data-approve')); }); });
  }
  function renderAudit(list) {
    var h = '<thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>对象</th><th>明细</th><th>TraceID</th></tr></thead><tbody>';
    list.forEach(function (a) {
      h += '<tr><td>' + esc(a.ts) + '</td><td>' + esc(a.actor) + '</td><td><span class="badge">' + esc(a.action) +
        '</span></td><td>' + esc(a.entity_id || '') + '</td><td style="white-space:normal;max-width:380px">' + esc(a.detail) +
        '</td><td>' + esc(a.trace_id) + '</td></tr>';
    });
    $('#auditTable').innerHTML = h + '</tbody>';
  }

  /* ---------------- 数据加载 ---------------- */
  function loadView(v) {
    if (v === 'dash') api('/api/dashboard').then(function (r) { renderDashboard(r.data); });
    else if (v === 'po') {
      api('/api/orders').then(function (r) { renderOrders(r.data); });
      if (!$('#poSku').options.length) fillSelects();
    } else if (v === 'inv') {
      api('/api/inventory').then(function (r) { renderInventory(r.data); });
      api('/api/audit').then(function (r) { /* 库存流水由 ledger 表在 ops 展示 */ });
    } else if (v === 'fin') api('/api/vouchers').then(function (r) { renderVouchers(r.data); });
    else if (v === 'ops') {
      api('/api/health').then(function (r) { $('#healthJson').textContent = JSON.stringify(r.data, null, 2); });
      api('/api/metrics').then(function (r) { $('#metricsJson').textContent = JSON.stringify(r.data, null, 2); });
      api('/api/audit').then(function (r) { renderAudit(r.data); });
    }
  }
  function fillSelects() {
    api('/api/materials').then(function (r) {
      $('#poSku').innerHTML = r.data.map(function (m) {
        return '<option value="' + m.sku + '"' + (m.sku === 'A1024' ? ' selected' : '') + '>' + esc(m.name) + '（库存/安全 ' + m.safety_stock + '）</option>';
      }).join('');
    });
    api('/api/suppliers').then(function (r) {
      $('#poSupplier').innerHTML = r.data.map(function (s) {
        return '<option value="' + s.id + '"' + (s.id === 'SUP-01' ? ' selected' : '') + '>' + esc(s.name) + ' · 准时 ' + s.on_time + '%</option>';
      }).join('');
    });
  }

  /* ---------------- 真实业务操作 ---------------- */
  function recommend() {
    return api('/api/ai/recommend', { method: 'POST', body: { sku: $('#poSku').value, qty: parseInt($('#poQty').value, 10) || 760 } })
      .then(function (r) {
        var d = r.data, h = '<div class="ai-card"><h4>✦ AI 供应商推荐 · ' + esc(d.material.name) + ' × ' + d.qty + '</h4>';
        d.recommendations.forEach(function (x, i) {
          h += '<div style="display:flex;gap:10px;align-items:center;background:#fff;border:1px solid #e6defb;border-radius:8px;padding:8px 10px;margin-bottom:6px">' +
            '<b style="width:110px;font-size:11.5px">' + (i + 1) + '. ' + esc(x.name) + '</b>' +
            '<div style="flex:1"><div class="bar"><i style="width:' + x.score + '%"></i></div>' +
            '<div class="mini" style="margin-top:3px">' + esc(x.explain) + ' · 单价 ' + esc(x.unit_price) + '</div></div>' +
            '<b style="color:#6d28d9;font-size:15px">' + x.score + '</b></div>';
        });
        h += '<div class="mini">数据源：' + d.data_sources.join(' / ') + ' · ' + esc(d.engine) + ' · <b>' + esc(d.gate) + '</b></div></div>';
        $('#recResult').innerHTML = h;
        return d;
      });
  }
  function createOrder() {
    return api('/api/orders', {
      method: 'POST', body: { sku: $('#poSku').value, qty: parseInt($('#poQty').value, 10) || 760, supplier_id: $('#poSupplier').value }
    }).then(function (r) {
      if (!r.ok) return toast('创建失败：' + r.data.error);
      toast('订单 ' + r.data.order_id + ' 已创建 · ' + r.data.amount + ' · trace ' + r.data.trace_id);
      api('/api/orders').then(function (x) { renderOrders(x.data); });
      return r.data;
    });
  }
  function receive(oid) {
    return api('/api/orders/' + oid + '/receive', { method: 'POST', body: {} }).then(function (r) {
      if (!r.ok) return toast('收货失败：' + r.data.error);
      toast('原子事务完成：库存 ' + r.data.inventory_before + ' → ' + r.data.inventory_after + '，凭证草稿 ' + r.data.voucher_draft);
      api('/api/orders').then(function (x) { renderOrders(x.data); });
      return r.data;
    });
  }
  function approve(vno) {
    return api('/api/vouchers/' + vno + '/approve', { method: 'POST', body: {} }).then(function (r) {
      if (!r.ok) return toast('硬规则拒绝：' + r.data.error);
      toast('凭证 ' + vno + ' 已过账，审计日志已写入');
      api('/api/vouchers').then(function (x) { renderVouchers(x.data); });
      return r.data;
    });
  }
  function ask(q) {
    $('#copilotLog').innerHTML = '';
    log('[用户] ' + q, 'y');
    return api('/api/copilot', { method: 'POST', body: { message: q } }).then(function (r) {
      var d = r.data;
      log('[规划] 意图识别完成，编排 ' + d.tools.length + ' 个工具', 'b');
      d.tools.forEach(function (t, i) { log('  └ ' + (i + 1) + '/' + d.tools.length + ' ' + t, 'g'); });
      log('[闸门] ' + d.gate, 'b');
      $('#copilotAnswer').innerHTML = '<div class="ai-card"><h4>✦ Copilot 回答</h4><p style="margin:0 0 8px;font-size:12.5px">' +
        esc(d.answer) + '</p><div class="mini">数据表：' + d.data_sources.join(' / ') + ' · TraceID ' + esc(d.trace_id) +
        ' · 每步写入 audit_logs</div></div>';
      return d;
    });
  }
  function log(txt, cls) {
    var d = document.createElement('div'); if (cls) d.className = cls; d.textContent = txt;
    $('#copilotLog').appendChild(d); $('#copilotLog').scrollTop = $('#copilotLog').scrollHeight;
  }

  $('#btnRecommend').addEventListener('click', recommend);
  $('#btnCreate').addEventListener('click', createOrder);
  $('#btnVoucher').addEventListener('click', function () {
    api('/api/ai/voucher-suggest', { method: 'POST', body: { memo: '秋季新品发布会会务费', amount_cents: 8640000 } })
      .then(function (r) {
        $('#voucherHint').textContent = '草稿 ' + r.data.voucher_no + ' 已生成 · ' + r.data.gate;
        toast('AI 凭证草稿 ' + r.data.voucher_no + ' 已生成，等待人工复核');
        api('/api/vouchers').then(function (x) { renderVouchers(x.data); });
      });
  });
  $('#btnAsk').addEventListener('click', function () { var q = $('#askInput').value.trim(); if (q) ask(q); });
  $('#askInput').addEventListener('keydown', function (e) { if (e.key === 'Enter') $('#btnAsk').click(); });
  $$('#chips .chip').forEach(function (c) {
    c.addEventListener('click', function () { $('#askInput').value = c.textContent; ask(c.textContent); });
  });

  /* ---------------- 健康检查与时钟 ---------------- */
  function health() {
    api('/api/health').then(function (r) {
      $('#healthPill').textContent = '● 服务在线';
      $('#sideStatus').textContent = '服务在线 · ' + r.data.database;
    }).catch(function () { $('#healthPill').textContent = '● 服务离线'; });
  }
  health();
  fillSelects();
  loadView('dash');
  setInterval(function () {
    if (DEMO) return;
    $('#clock').textContent = new Date().toTimeString().slice(0, 8);
  }, 1000);
  $('#clock').textContent = new Date().toTimeString().slice(0, 8);

  /* =====================================================================
   * 演示录制模式：window.renderAt(t) 是纯时间函数，禁用一切动画，逐帧可复现。
   * 真实写操作只在 setup 阶段执行一次（带幂等键），前后快照按时间轴切换显示。
   * ===================================================================== */
  var SNAP = { before: {}, after: {}, copilot: null, rec: null, receipt: null, voucher: null };
  var STEPS = [
    { t: 0, view: 'dash', cursor: [520, 220], label: '' },
    { t: 3.0, view: 'dash', cursor: [750, 210], flash: '.stat:nth-child(2)' },
    { t: 5.5, view: 'dash', cursor: [980, 420], flash: '.stat:nth-child(4)' },
    { t: 7.5, view: 'po', cursor: [300, 300], flash: '.nav[data-view="po"]' },
    { t: 9.0, view: 'po', cursor: [420, 300], qty: '760' },
    { t: 10.5, view: 'po', cursor: [560, 300], click: '#btnRecommend' },
    { t: 12.5, view: 'po', cursor: [700, 420], showRec: true },
    { t: 15.0, view: 'po', cursor: [760, 300], click: '#btnCreate', toast: '订单已创建 · 真实写入 orders 表' },
    { t: 18.0, view: 'po', cursor: [1080, 560], click: '[data-receive]', state: 'after', toast: '原子事务：库存 +760 / 凭证草稿已生成' },
    { t: 21.0, view: 'inv', cursor: [520, 330], flash: '.nav[data-view="inv"]' },
    { t: 23.5, view: 'fin', cursor: [300, 300], flash: '.nav[data-view="fin"]' },
    { t: 25.5, view: 'fin', cursor: [420, 330], click: '#btnVoucher', toast: 'AI 凭证草稿已生成 · 置信度 0.88' },
    { t: 28.5, view: 'fin', cursor: [1080, 520], click: '[data-approve]', toast: '硬规则校验通过 · 凭证已过账' },
    { t: 31.0, view: 'ai', cursor: [420, 300], flash: '.nav[data-view="ai"]' },
    { t: 32.5, view: 'ai', cursor: [500, 350], typeText: '查询低于安全水位的库存并生成补货任务' },
    { t: 36.0, view: 'ai', cursor: [900, 350], click: '#btnAsk', copilot: true },
    { t: 39.0, view: 'ops', cursor: [520, 330], flash: '.nav[data-view="ops"]' },
    { t: 42.0, view: 'dash', cursor: [520, 220] }
  ];

  function snapshot(target) {
    return Promise.all([
      api('/api/dashboard'), api('/api/orders'), api('/api/inventory'),
      api('/api/vouchers'), api('/api/audit'), api('/api/health'), api('/api/metrics')
    ]).then(function (rs) {
      target.dash = rs[0].data; target.orders = rs[1].data; target.inv = rs[2].data;
      target.vouchers = rs[3].data; target.audit = rs[4].data; target.health = rs[5].data; target.metrics = rs[6].data;
    });
  }

  function setupDemo() {
    return snapshot(SNAP.before)
      .then(function () { return api('/api/ai/recommend', { method: 'POST', body: { sku: 'A1024', qty: 760 } }); })
      .then(function (r) {
        SNAP.rec = r.data;
        return api('/api/orders', { method: 'POST', body: { sku: 'A1024', qty: 760, supplier_id: 'SUP-01', idempotency_key: 'video-demo-20261107' } });
      })
      .then(function (r) {
        SNAP.newOrder = r.data.order_id;
        return api('/api/orders/' + r.data.order_id + '/receive', { method: 'POST', body: { idempotency_key: 'video-demo-receive-20261107' } });
      })
      .then(function (r) {
        SNAP.receipt = r.data;
        return api('/api/ai/voucher-suggest', { method: 'POST', body: { memo: '秋季新品发布会会务费', amount_cents: 8640000, id: 'EV-DEMO-0041' } });
      })
      .then(function (r) {
        SNAP.voucher = r.data;
        return api('/api/vouchers/' + r.data.voucher_no + '/approve', { method: 'POST', body: { idempotency_key: 'video-demo-approve-20261107' } });
      })
      .then(function () { return api('/api/copilot', { method: 'POST', body: { message: '查询低于安全水位的库存并生成补货任务' } }); })
      .then(function (r) { SNAP.copilot = r.data; return snapshot(SNAP.after); })
      .then(function () { window.__REC_READY = true; });
  }

  function applyState(which, view) {
    var s = SNAP[which];
    if (!s) return;
    if (view === 'dash') renderDashboard(s.dash);
    else if (view === 'po') renderOrders(s.orders);
    else if (view === 'inv') renderInventory(s.inv);
    else if (view === 'fin') renderVouchers(s.vouchers);
    else if (view === 'ops') {
      renderAudit(s.audit);
      $('#healthJson').textContent = JSON.stringify(s.health, null, 2);
      $('#metricsJson').textContent = JSON.stringify(s.metrics, null, 2);
    }
  }

  var lastKey = '';
  function renderAt(t) {
    if (!window.__REC_READY) return;
    var u = Math.min(NOMINAL - 0.01, t * NOMINAL / (D || NOMINAL)); // 按真实页时长归一化
    var idx = 0;
    for (var i = 0; i < STEPS.length; i++) { if (u >= STEPS[i].t) idx = i; }
    var s = STEPS[idx], next = STEPS[idx + 1] || s;
    var key = idx + '|' + s.view;
    if (key !== lastKey) {
      lastKey = key;
      switchView(s.view);
      $$('.flash').forEach(function (el) { el.classList.remove('flash'); });
      if (s.flash) { var f = $(s.flash); if (f) f.classList.add('flash'); }
      if (s.qty) $('#poQty').value = s.qty;
      var state = 'before', showRec = false;
      for (var j = 0; j <= idx; j++) {
        if (STEPS[j].state) state = STEPS[j].state;
        if (STEPS[j].showRec) showRec = true;
      }
      applyState(state, s.view);
      if (showRec && SNAP.rec) {
        var d = SNAP.rec, h = '<div class="ai-card"><h4>✦ AI 供应商推荐 · A1024 × 760</h4>';
        d.recommendations.forEach(function (x, i2) {
          h += '<div style="display:flex;gap:10px;align-items:center;background:#fff;border:1px solid #e6defb;border-radius:8px;padding:7px 10px;margin-bottom:5px">' +
            '<b style="width:100px;font-size:11.5px">' + (i2 + 1) + '. ' + esc(x.name) + '</b>' +
            '<div style="flex:1"><div class="bar"><i style="width:' + x.score + '%"></i></div>' +
            '<div class="mini" style="margin-top:3px">' + esc(x.explain) + '</div></div>' +
            '<b style="color:#6d28d9;font-size:14px">' + x.score + '</b></div>';
        });
        h += '<div class="mini">数据源 suppliers / orders / materials · <b>建议态，需人工确认</b></div></div>';
        $('#recResult').innerHTML = h;
      } else if (s.view === 'po') $('#recResult').innerHTML = '';
      if (s.click) { var c = $(s.click); if (c) c.classList.add('flash'); }
      if (s.toast) toast(s.toast); else $('#toast').classList.remove('on');
      if (s.typeText !== undefined) $('#askInput').value = '';
      if (s.copilot && SNAP.copilot) {
        $('#askInput').value = '查询低于安全水位的库存并生成补货任务';
        $('#toast').textContent = 'Copilot 编排完成 · 高风险动作已挂起';
        $('#toast').classList.add('on');
        var cpd = SNAP.copilot;
        $('#copilotLog').innerHTML = '';
        log('[用户] 查询低于安全水位的库存并生成补货任务', 'y');
        log('[规划] 意图识别完成，编排 ' + cpd.tools.length + ' 个工具', 'b');
        cpd.tools.forEach(function (x, k) { log('  └ ' + (k + 1) + '/' + cpd.tools.length + ' ' + x, 'g'); });
        log('[闸门] ' + cpd.gate, 'b');
        $('#copilotAnswer').innerHTML = '<div class="ai-card"><h4>✦ Copilot 回答</h4><p style="margin:0 0 6px;font-size:12.5px">' +
          esc(cpd.answer) + '</p><div class="mini">TraceID ' + esc(cpd.trace_id) + ' · 每步写入 audit_logs</div></div>';
      }
    }
    // 打字效果：由 t 确定性计算，零抖动
    if (s.typeText !== undefined) {
      var p = Math.min(1, Math.max(0, (u - s.t) / Math.max(0.1, next.t - s.t)));
      $('#askInput').value = s.typeText.slice(0, Math.floor(s.typeText.length * p));
    }
    // 光标位置：相邻步骤间线性插值
    var seg = Math.max(0.1, (next.t - s.t));
    var p2 = Math.min(1, Math.max(0, (u - s.t) / seg));
    var cx = s.cursor[0] + (next.cursor[0] - s.cursor[0]) * p2;
    var cy = s.cursor[1] + (next.cursor[1] - s.cursor[1]) * p2;
    var cur = $('#cursor'); cur.style.left = cx + 'px'; cur.style.top = cy + 'px';
    var sec = 30 + Math.floor(u);
    $('#clock').textContent = '23:' + (30 + Math.floor(sec / 60)) + ':' + String(sec % 60).padStart(2, '0');
  }

  if (DEMO) {
    window.renderAt = renderAt;
    window.__REC_READY = false;
    setupDemo().catch(function (e) {
      console.error('demo setup failed', e);
      window.__REC_READY = true; // 录制不因网络抖动中断
    });
  }
})();
