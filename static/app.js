(function () {
  'use strict';

  // ── 页面检测：manage/portfolio/fund-detail 不启动自动刷新 ──
  const path = window.location.pathname;
  const isHomePage = path === '/';
  if (!isHomePage) return;

  // ── 读取自动刷新间隔 ──
  const refreshSelect = document.getElementById('refresh-select');
  const searchInput = document.getElementById('search-input');
  const sortSelect = document.getElementById('sort-select');
  const listContainer = document.getElementById('fund-list-container');

  let refreshTimer = null;
  let searchFocused = false;
  let blurTimer = null;

  function getRefreshSeconds() {
    if (!refreshSelect) return 0;
    const v = Number(refreshSelect.value);
    return isNaN(v) ? 0 : v;
  }

  // ── 局部刷新：fetch /api/live-estimates ──
  function fetchAndUpdate() {
    if (!listContainer) return;
    const search = searchInput ? searchInput.value.trim() : '';
    const sort = sortSelect ? sortSelect.value : 'estimate_desc';
    const params = new URLSearchParams({ search, sort });
    fetch('/api/live-estimates?' + params.toString())
      .then(function (r) { return r.json(); })
      .then(function (data) { renderList(data); })
      .catch(function () { /* 静默失败，不破坏页面 */ });
  }

  function renderList(data) {
    if (!listContainer) return;
    if (!data.rows || data.rows.length === 0) {
      listContainer.innerHTML = '<div class="empty-panel">当前没有可展示的基金估值结果。</div>';
      return;
    }
    // 更新状态栏
    const statusEl = document.getElementById('status-message');
    if (statusEl && data.status_message) statusEl.textContent = data.status_message;
    const latestTimeEl = document.getElementById('latest-time');
    if (latestTimeEl && data.latest_time) latestTimeEl.textContent = data.latest_time;

    listContainer.innerHTML = data.rows.map(function (row) {
      var confidenceClass = 'badge-' + (row.confidence_level || 'd').toLowerCase();
      var estimateTone = row.estimate_tone || 'muted';
      var profitTone = row.profit_tone || 'muted';
      var tags = row.fund_code;
      if (row.is_watchlist) tags += ' · 自选';
      tags += row.is_holding ? ' · 持有' : ' · 观察';
      tags += ' · ' + (row.quote_time || '--');
      return '<a class="fund-row" href="/fund/' + row.fund_code + '">' +
        '<div class="fund-main">' +
          '<div class="fund-name">' + escHtml(row.fund_name) + '</div>' +
          '<div class="fund-sub">' + escHtml(tags) + '</div>' +
        '</div>' +
        '<div class="fund-value ' + estimateTone + '">' + escHtml(row.current_estimate_text) + '</div>' +
        '<div class="fund-profit ' + profitTone + '">' + escHtml(row.estimated_today_profit_text) + '</div>' +
        '<div class="fund-badge ' + confidenceClass + '">' + escHtml(row.confidence_level || 'D') + '</div>' +
      '</a>';
    }).join('');
  }

  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // ── 定时器管理 ──
  function startTimer() {
    stopTimer();
    var secs = getRefreshSeconds();
    if (!secs || secs <= 0) return;
    refreshTimer = setTimeout(function () {
      if (!searchFocused) {
        fetchAndUpdate();
        startTimer();
      }
    }, secs * 1000);
  }

  function stopTimer() {
    if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
  }

  // ── 搜索框防打断 ──
  if (searchInput) {
    searchInput.addEventListener('focus', function () {
      searchFocused = true;
      if (blurTimer) { clearTimeout(blurTimer); blurTimer = null; }
      stopTimer();
    });
    searchInput.addEventListener('blur', function () {
      blurTimer = setTimeout(function () {
        searchFocused = false;
        startTimer();
      }, 2000);
    });
  }

  // ── 刷新选择器变化时重置定时器 ──
  if (refreshSelect) {
    refreshSelect.addEventListener('change', function () {
      startTimer();
    });
  }

  // ── 初始启动 ──
  startTimer();

  // ── manage 页 Tab 切换（若在 manage 页执行到这里则忽略，path 检测已 return） ──
})();

// ── manage 页 Tab 切换（全局函数） ──
function switchTab(tabId) {
  document.querySelectorAll('.tab-panel').forEach(function (el) {
    el.style.display = 'none';
  });
  document.querySelectorAll('.tab-btn').forEach(function (el) {
    el.classList.remove('active');
  });
  var panel = document.getElementById(tabId);
  if (panel) panel.style.display = 'block';
  var btn = document.querySelector('[data-tab="' + tabId + '"]');
  if (btn) btn.classList.add('active');
  try { localStorage.setItem('manage_tab', tabId); } catch(e) {}
}

// ── manage 页初始化 Tab ──
(function () {
  if (window.location.pathname !== '/manage') return;
  var panels = document.querySelectorAll('.tab-panel');
  if (!panels.length) return;
  panels.forEach(function (el) { el.style.display = 'none'; });
  var saved = '';
  try { saved = localStorage.getItem('manage_tab') || ''; } catch(e) {}
  var first = saved && document.getElementById(saved) ? saved : (panels[0] && panels[0].id);
  if (first) switchTab(first);
})();

// ── 持仓行管理 ──
function addHoldingRow() {
  var tbody = document.getElementById('holding-items-tbody');
  if (!tbody) return;
  var idx = tbody.querySelectorAll('tr').length;
  var tr = document.createElement('tr');
  tr.innerHTML =
    '<td><input type="text" name="asset_code_' + idx + '" placeholder="600988.SH" style="width:100%"></td>' +
    '<td><input type="text" name="asset_name_' + idx + '" placeholder="股票名称" style="width:100%"></td>' +
    '<td><input type="text" name="asset_type_' + idx + '" placeholder="stock" value="stock" style="width:100%"></td>' +
    '<td><input type="number" step="0.01" name="weight_pct_' + idx + '" placeholder="9.50" style="width:100%"></td>' +
    '<td><button type="button" class="btn-danger btn-sm" onclick="removeRow(this)">删除</button></td>';
  tbody.appendChild(tr);
}

function removeRow(btn) {
  var tr = btn.closest('tr');
  if (tr) tr.remove();
  updateWeightSum();
}

function updateWeightSum() {
  var inputs = document.querySelectorAll('[name^="weight_pct_"]');
  var total = 0;
  inputs.forEach(function (inp) {
    var v = parseFloat(inp.value);
    if (!isNaN(v)) total += v;
  });
  var el = document.getElementById('weight-sum');
  if (el) {
    el.textContent = '权重合计: ' + total.toFixed(2) + '%';
    el.style.color = total > 100 ? '#f05555' : '#2c5ff6';
  }
}

document.addEventListener('input', function (e) {
  if (e.target && e.target.name && e.target.name.startsWith('weight_pct_')) {
    updateWeightSum();
  }
});
