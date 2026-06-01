(function () {
  'use strict';
  console.log("Fund estimator frontend loaded");

  // ══════════════════════════════════════════════════════════════════════
  // § 1. 基本配置 & 页面检测
  // ══════════════════════════════════════════════════════════════════════
  var path = window.location.pathname;
  var isHomePage = path === '/';

  // ══════════════════════════════════════════════════════════════════════
  // § 2. Tab 切换（管理页）
  // ══════════════════════════════════════════════════════════════════════
  window.switchTab = function (tabId) {
    document.querySelectorAll('.tab-panel').forEach(function (el) {
      el.style.display = 'none';
    });
    document.querySelectorAll('.tab-btn').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.tab === tabId);
    });
    var panel = document.getElementById(tabId);
    if (panel) panel.style.display = 'block';
  };
  // 初始化：显示第一个 tab
  (function () {
    var panels = document.querySelectorAll('.tab-panel');
    var btns = document.querySelectorAll('.tab-btn');
    if (!panels.length) return;
    panels.forEach(function (el) { el.style.display = 'none'; });
    panels[0].style.display = 'block';
    if (btns.length) btns[0].classList.add('active');
  })();

  // ══════════════════════════════════════════════════════════════════════
  // § 3. 持仓行增删（管理页）
  // ══════════════════════════════════════════════════════════════════════
  var rowIndex = 10;

  window.addHoldingRow = function () {
    var tbody = document.getElementById('holding-items-tbody');
    if (!tbody) return;
    var tr = document.createElement('tr');
    tr.innerHTML = [
      '<td><input type="text" name="asset_code_' + rowIndex + '" placeholder="600988.SH" style="width:100%"></td>',
      '<td><input type="text" name="asset_name_' + rowIndex + '" placeholder="股票名称" style="width:100%"></td>',
      '<td><input type="text" name="asset_type_' + rowIndex + '" placeholder="stock" value="stock" style="width:100%"></td>',
      '<td><input type="number" step="0.01" name="weight_pct_' + rowIndex + '" placeholder="9.50" style="width:100%" oninput="updateWeightSum()"></td>',
      '<td><button type="button" class="btn-danger btn-sm btn" onclick="removeRow(this)">删除</button></td>',
    ].join('');
    tbody.appendChild(tr);
    rowIndex++;
  };

  window.removeRow = function (btn) {
    var tr = btn.closest('tr');
    if (tr) { tr.remove(); updateWeightSum(); }
  };

  window.updateWeightSum = function () {
    var sumEl = document.getElementById('weight-sum');
    if (!sumEl) return;
    var inputs = document.querySelectorAll('[name^="weight_pct_"]');
    var total = 0;
    inputs.forEach(function (inp) {
      var v = parseFloat(inp.value);
      if (!isNaN(v)) total += v;
    });
    sumEl.textContent = total > 0 ? '权重合计：' + total.toFixed(2) + '%' : '';
    sumEl.style.color = Math.abs(total - 100) < 0.1 ? '#16a34a' : (total > 0 ? '#2563eb' : '#2563eb');
  };

  // ══════════════════════════════════════════════════════════════════════
  // § 4. 首页：新基金搜索面板
  // ══════════════════════════════════════════════════════════════════════
  var searchInput = document.getElementById('search-input');
  var newFundPanel = document.getElementById('new-fund-panel');
  var newFundInfo = document.getElementById('new-fund-info');
  var newFundResult = document.getElementById('new-fund-result');
  var currentSearchCode = '';

  // 判断输入是否像基金代码（纯6位数字）
  function looksLikeFundCode(s) {
    return /^\d{6}$/.test(s.trim());
  }

  var searchTimer = null;

  function hideNewFundPanel() {
    if (newFundPanel) newFundPanel.style.display = 'none';
    currentSearchCode = '';
  }

  function doFundCodeSearch(code) {
    if (!newFundPanel) return;
    currentSearchCode = code;
    fetch('/api/search-fund?code=' + encodeURIComponent(code))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.found) {
          newFundPanel.style.display = 'block';
          newFundInfo.innerHTML = '<strong>' + code + '</strong> — 基金信息拉取失败，请确认代码正确。';
          newFundResult.textContent = '';
          return;
        }
        var name = data.fund_name || code;
        var nav = data.latest_unit_nav ? ('最新净值：' + data.latest_unit_nav + '（' + (data.latest_nav_date || '') + '）') : '';
        if (data.in_db) {
          var statusParts = [];
          if (data.in_watchlist) statusParts.push('✓ 已在自选');
          if (data.has_position) statusParts.push('✓ 已有持仓 ¥' + (data.holding_amount || '--'));
          newFundPanel.style.display = 'block';
          newFundInfo.innerHTML = '<strong>' + name + '</strong>（' + code + '）' +
            (statusParts.length ? ' &nbsp;·&nbsp; ' + statusParts.join('、') : '') +
            (nav ? '<br><span style="color:#64748b;font-size:12px">' + nav + '</span>' : '');
        } else {
          newFundPanel.style.display = 'block';
          newFundInfo.innerHTML = '<strong>' + name + '</strong>（' + code + '）&nbsp;·&nbsp; 未添加到本地' +
            (nav ? '<br><span style="color:#64748b;font-size:12px">' + nav + '</span>' : '');
        }
        newFundResult.textContent = '';
      })
      .catch(function () { 
        newFundPanel.style.display = 'block';
        newFundInfo.innerHTML = '<strong>' + code + '</strong> — 行情源暂时失败或未找到基金。';
        newFundResult.textContent = '';
      })
      .finally(function () {
        if (searchBtn) {
          searchBtn.textContent = "搜索";
          searchBtn.disabled = false;
        }
      });
  }

  var searchBtn = document.getElementById('search-btn');

  function triggerSearch() {
    var val = searchInput.value.trim();
    if (!val) return;
    if (looksLikeFundCode(val)) {
      searchBtn.textContent = "搜索中...";
      searchBtn.disabled = true;
      doFundCodeSearch(val);
    } else {
      if (typeof fetchAndUpdateList === 'function') fetchAndUpdateList();
    }
  }

  if (searchInput && isHomePage) {
    searchInput.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') {
        e.preventDefault();
        triggerSearch();
      }
    });
    
    if (searchBtn) {
      searchBtn.addEventListener('click', function(e) {
        e.preventDefault();
        triggerSearch();
      });
    }

    searchInput.addEventListener('input', function () {
      var val = searchInput.value.trim();
      clearTimeout(searchTimer);
      if (!looksLikeFundCode(val)) {
        hideNewFundPanel();
        return;
      }
      // 不自动搜索了，等用户按回车或搜索按钮
      // 但如果是 6位 数字，可以延迟给一点提示
    });
    searchInput.addEventListener('blur', function () {
      setTimeout(function () {
        var val = searchInput.value.trim();
        if (!looksLikeFundCode(val)) hideNewFundPanel();
      }, 300);
    });
  }

  // 加入自选
  var btnQuickAdd = document.getElementById('btn-quick-add');
  if (btnQuickAdd) {
    btnQuickAdd.addEventListener('click', function () {
      var code = currentSearchCode || (searchInput && searchInput.value.trim());
      if (!code) return;
      btnQuickAdd.disabled = true;
      btnQuickAdd.textContent = '加入中…';
      fetch('/api/quick-add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fund_code: code }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            newFundResult.textContent = '✓ 已加入自选：' + (data.fund_name || code);
            newFundResult.style.color = '#16a34a';
            setTimeout(function () { window.location.reload(); }, 1200);
          } else {
            newFundResult.textContent = '失败：' + (data.error || '未知错误');
            newFundResult.style.color = '#dc2626';
            btnQuickAdd.disabled = false;
            btnQuickAdd.textContent = '加入自选';
          }
        })
        .catch(function (e) {
          newFundResult.textContent = '请求失败：' + e.message;
          newFundResult.style.color = '#dc2626';
          btnQuickAdd.disabled = false;
          btnQuickAdd.textContent = '加入自选';
        });
    });
  }

  // 按金额买入
  var btnQuickBuy = document.getElementById('btn-quick-buy');
  if (btnQuickBuy) {
    btnQuickBuy.addEventListener('click', function () {
      var code = currentSearchCode || (searchInput && searchInput.value.trim());
      var amountEl = document.getElementById('quick-buy-amount');
      var amount = amountEl ? parseFloat(amountEl.value) : NaN;
      if (!code) { alert('请先输入基金代码'); return; }
      if (!amount || amount <= 0) { alert('请输入正确的持有金额'); return; }
      btnQuickBuy.disabled = true;
      btnQuickBuy.textContent = '保存中…';
      fetch('/api/quick-buy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fund_code: code, holding_amount: amount }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            newFundResult.textContent = '✓ 已添加持仓：' + (data.fund_name || code) + ' ¥' + amount;
            newFundResult.style.color = '#16a34a';
            setTimeout(function () { window.location.reload(); }, 1200);
          } else {
            newFundResult.textContent = '失败：' + (data.error || '未知错误');
            newFundResult.style.color = '#dc2626';
            btnQuickBuy.disabled = false;
            btnQuickBuy.textContent = '按金额买入';
          }
        })
        .catch(function (e) {
          newFundResult.textContent = '请求失败：' + e.message;
          newFundResult.style.color = '#dc2626';
          btnQuickBuy.disabled = false;
          btnQuickBuy.textContent = '按金额买入';
        });
    });
  }

  // ══════════════════════════════════════════════════════════════════════
  // § 5. 首页：JSON 局部刷新（不打断输入）
  // ══════════════════════════════════════════════════════════════════════
  if (!isHomePage) return;

  var refreshSelect = document.getElementById('refresh-select');
  var sortSelect = document.getElementById('sort-select');
  var listContainer = document.getElementById('fund-list-container');
  var statusMsg = document.getElementById('status-message');
  var latestTime = document.getElementById('latest-time');

  var paused = false;
  var refreshTimer = null;
  var FUND_CODE_RE = /^\d{6}$/;

  function getRefreshInterval() {
    if (!refreshSelect) return 0;
    var v = parseInt(refreshSelect.value, 10);
    return isNaN(v) ? 0 : v * 1000;
  }

  function tone(v) {
    if (v === undefined || v === null) return 'muted';
    var f = parseFloat(v);
    if (isNaN(f)) return 'muted';
    return f > 0 ? 'up' : (f < 0 ? 'down' : 'muted');
  }

  function esc(v) {
    return String(v === undefined || v === null ? '' : v)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function renderHoldingRow(row) {
    var pendingNote = row.has_pending_today_event ? ' · 今日交易下个交易日计盈亏' : '';
    return [
      '<a class="fund-row fund-row-holding" href="/fund/' + esc(row.fund_code) + '">',
      '<div class="fund-main">',
      '<div class="fund-name">' + esc(row.fund_name || row.fund_code) + '</div>',
      '<div class="fund-sub">' + esc(row.fund_code) + ' · 持有 · ' + esc(row.quote_time || '--') + pendingNote + '</div>',
      '</div>',
      '<div class="fund-value ' + esc(row.estimate_tone || 'muted') + '">' + esc(row.current_estimate_text || '--') + '</div>',
      '<div class="fund-compare ' + esc(row.actual_return_tone || 'muted') + '">' + (row.actual_return_available ? esc(row.actual_return_today_text) : '--') + '</div>',
      '<div class="fund-amount">¥' + esc(row.holding_amount_text || '--') + '</div>',
      '<div class="fund-profit ' + esc(row.profit_tone || 'muted') + '">' + esc(row.estimated_today_profit_text || '--') + '</div>',
      '<div class="fund-error reliability-' + esc(row.reliability_tone || 'muted') + '">' + esc(row.reliability_label || row.error_band_short || '样本不足') + '</div>',
      '<div class="fund-spark"><canvas class="spark-canvas" data-fund="' + esc(row.fund_code) + '" title="' + esc(row.fund_name || row.fund_code) + ' · 点击查看详情大图"></canvas></div>',
      '</a>',
    ].join('');
  }

  function renderWatchRow(row) {
    return [
      '<a class="fund-row fund-row-watch" href="/fund/' + esc(row.fund_code) + '">',
      '<div class="fund-main">',
      '<div class="fund-name">' + esc(row.fund_name || row.fund_code) + '</div>',
      '<div class="fund-sub">' + esc(row.fund_code) + ' · 自选 · ' + esc(row.quote_time || '--') + '</div>',
      '</div>',
      '<div class="fund-value ' + esc(row.estimate_tone || 'muted') + '">' + esc(row.current_estimate_text || '--') + '</div>',
      '<div class="fund-compare ' + esc(row.actual_return_tone || 'muted') + '">' + (row.actual_return_available ? esc(row.actual_return_today_text) : '--') + '</div>',
      '<div class="fund-error reliability-' + esc(row.reliability_tone || 'muted') + '">' + esc(row.reliability_label || row.error_band_short || '样本不足') + '</div>',
      '<div class="fund-spark"><canvas class="spark-canvas" data-fund="' + esc(row.fund_code) + '" title="' + esc(row.fund_name || row.fund_code) + ' · 点击查看详情大图"></canvas></div>',
      '</a>',
    ].join('');
  }

  function renderFundList(data) {
    if (!listContainer) return;
    var holdingRows = data && data.holding_rows ? data.holding_rows : [];
    var watchlistRows = data && data.watchlist_rows ? data.watchlist_rows : [];
    var otherRows = data && data.other_rows ? data.other_rows : [];

    var totalEl = document.getElementById('total-today-profit');
    if (totalEl && data) {
      totalEl.textContent = data.total_today_profit_text || '--';
      totalEl.className = data.total_today_profit_tone || 'muted';
    }

    if (holdingRows.length === 0 && watchlistRows.length === 0 && otherRows.length === 0) {
      listContainer.innerHTML = '<div class="empty-panel">当前没有可展示的基金估值结果。<br>请在搜索框输入基金代码，加入自选或按金额买入。</div>';
      return;
    }

    var html = [];

    html.push('<section class="fund-section" data-section="holding">');
    html.push('<div style="display:flex; justify-content:space-between; align-items:flex-end; margin:10px 4px 6px;"><div style="font-size:15px; font-weight:800; color:#0f172a;">我的持仓</div></div>');
    html.push('<div class="fund-list-header fund-header-holding"><div class="header-main">基金名称</div><div class="header-col">实时估值</div><div class="header-col">实际收盘</div><div class="header-col">持有金额</div><div class="header-col">今日盈亏</div><div class="header-col">可靠性</div><div class="header-col">分时走势</div></div>');
    html.push('<div id="holding-list">');
    html.push(holdingRows.length ? holdingRows.map(renderHoldingRow).join('') : '<div class="empty-panel">暂无持有基金。搜索基金代码后可以按金额买入。</div>');
    html.push('</div></section>');

    html.push('<section class="fund-section" data-section="watchlist" style="margin-top:14px;">');
    html.push('<div style="display:flex; justify-content:space-between; align-items:flex-end; margin:10px 4px 6px;"><div style="font-size:15px; font-weight:800; color:#0f172a;">自选观察</div></div>');
    html.push('<div class="fund-list-header fund-header-watch"><div class="header-main">基金名称</div><div class="header-col">实时估值</div><div class="header-col">实际收盘</div><div class="header-col">可靠性</div><div class="header-col">分时走势</div></div>');
    html.push('<div id="watchlist-list">');
    html.push(watchlistRows.length ? watchlistRows.map(renderWatchRow).join('') : '<div class="empty-panel">暂无仅自选基金。持有基金会自动加入自选，但只显示在“我的持仓”。</div>');
    html.push('</div></section>');

    if (otherRows.length) {
      html.push('<section class="fund-section" data-section="other" style="margin-top:14px;"><div style="font-size:15px; font-weight:800; color:#0f172a; margin:10px 4px 6px;">其他结果</div>');
      html.push(otherRows.map(renderWatchRow).join(''));
      html.push('</section>');
    }

    listContainer.innerHTML = html.join('');
  }

  function fetchAndUpdateList() {
    if (paused) return scheduleNext();
    var kw = searchInput ? searchInput.value.trim() : '';
    var sort = sortSelect ? sortSelect.value : 'estimate_desc';
    // 如果是纯6位数字代码，新基金搜索模式，不局部刷新列表
    if (FUND_CODE_RE.test(kw)) return scheduleNext();
    var url = '/api/live-estimates?search=' + encodeURIComponent(kw) + '&sort=' + encodeURIComponent(sort);
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        renderFundList(data);
        if (typeof window._onLiveRefresh === 'function') window._onLiveRefresh();
        if (statusMsg) statusMsg.textContent = data.status_message || '';
        if (latestTime) latestTime.textContent = data.latest_time || '--';
        scheduleNext();
      })
      .catch(function () { scheduleNext(); });
  }

  function scheduleNext() {
    clearTimeout(refreshTimer);
    var interval = getRefreshInterval();
    if (interval > 0) {
      refreshTimer = setTimeout(fetchAndUpdateList, interval);
    }
  }

  // 输入框 focus 暂停，blur 后延迟 2s 恢复
  if (searchInput) {
    searchInput.addEventListener('focus', function () { paused = true; clearTimeout(refreshTimer); });
    searchInput.addEventListener('blur', function () {
      setTimeout(function () { paused = false; scheduleNext(); }, 2000);
    });
    // 金额输入框 focus 也暂停
    var amountInput = document.getElementById('quick-buy-amount');
    if (amountInput) {
      amountInput.addEventListener('focus', function () { paused = true; clearTimeout(refreshTimer); });
      amountInput.addEventListener('blur', function () {
        setTimeout(function () { paused = false; scheduleNext(); }, 2000);
      });
    }
    
    // 实时搜索过滤（非代码模式）
    searchInput.addEventListener('input', function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () {
        var kw = searchInput.value.trim();
        if (!looksLikeFundCode(kw)) {
          // 触发局部刷新（带搜索词）
          fetchAndUpdateList();
        }
      }, 400);
    });
  }

  // sort 切换立即刷新
  if (sortSelect) {
    sortSelect.addEventListener('change', function () { fetchAndUpdateList(); });
  }

  // refresh interval 切换
  if (refreshSelect) {
    refreshSelect.addEventListener('change', function () { clearTimeout(refreshTimer); scheduleNext(); });
  }

  scheduleNext();

})();
