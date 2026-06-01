/**
 * charts.js — 轻量 Canvas 图表库（无外部依赖）
 *
 * 导出函数:
 *  drawIntradaySparkline(canvas, points)          — 首页迷你分时缩略图
 *  drawIntradayChart(canvas, points, mode, amt)   — 详情页完整盘中分时图
 *  drawFundChart(canvas, points, mode)            — 详情页近30日走势图
 *  drawPortfolioChart(canvas, pts, mode, holding) — 组合近30日柱状图
 */

'use strict';

// ── 颜色 ────────────────────────────────────────────────────
const C = {
  up:        '#e63946',
  down:      '#16a34a',
  estimate:  '#2563eb',
  zero:      '#cbd5e1',
  grid:      '#f1f5f9',
  axis:      '#94a3b8',
  lunch:     '#e2e8f0',
  bg:        '#ffffff',
};

// ── 交易时段常量 ─────────────────────────────────────────────
const MORNING_START_MIN  = 9  * 60 + 30;   // 09:30 → minute-of-day
const MORNING_END_MIN    = 11 * 60 + 30;   // 11:30
const AFTERNOON_START_MIN = 13 * 60;       // 13:00
const AFTERNOON_END_MIN   = 15 * 60;       // 15:00
const MORNING_MINS       = MORNING_END_MIN    - MORNING_START_MIN;  // 120
const AFTERNOON_MINS     = AFTERNOON_END_MIN  - AFTERNOON_START_MIN; // 120

/** "HH:MM:SS" → 交易分钟索引 (0..239)，午休返回 null */
function tradingMinIdx(timeStr) {
  const p = timeStr.split(':').map(Number);
  const totalMin = p[0] * 60 + p[1];
  if (totalMin >= MORNING_START_MIN && totalMin < MORNING_END_MIN)
    return totalMin - MORNING_START_MIN;               // 0..119
  if (totalMin >= AFTERNOON_START_MIN && totalMin <= AFTERNOON_END_MIN)
    return MORNING_MINS + (totalMin - AFTERNOON_START_MIN); // 120..240
  return null;
}

/** 交易分钟索引 → canvas X 坐标（午休间隔由 lunchGap 控制） */
function tradingIdxToX(idx, left, right, lunchGapPx) {
  const morningW = (right - left - lunchGapPx) / 2;
  if (idx <= MORNING_MINS) {
    return left + (idx / MORNING_MINS) * morningW;
  }
  const aftIdx = idx - MORNING_MINS;
  return left + morningW + lunchGapPx + (aftIdx / AFTERNOON_MINS) * morningW;
}

// ── 通用工具 ────────────────────────────────────────────────
function fmtPct(v) { return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'; }
function fmtAmt(v) { return (v >= 0 ? '+' : '') + v.toFixed(2) + '元'; }

function calcLayout(canvas) {
  const dpr  = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const W    = rect.width  * dpr;
  const H    = rect.height * dpr;
  canvas.width  = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  return { ctx, W: rect.width, H: rect.height };
}

function computeRange(vals, padFraction = 0.15) {
  let mn = Infinity, mx = -Infinity;
  for (const v of vals) {
    if (v != null && isFinite(v)) { mn = Math.min(mn, v); mx = Math.max(mx, v); }
  }
  if (!isFinite(mn)) return { min: -0.5, max: 0.5 };
  const range = mx - mn || 0.5;
  // 确保零线在可见范围内
  mn = Math.min(mn - range * padFraction, 0 - range * 0.05);
  mx = Math.max(mx + range * padFraction, 0 + range * 0.05);
  return { min: mn, max: mx };
}

function yVal(v, range, top, bottom) {
  const frac = (v - range.min) / (range.max - range.min);
  return bottom - frac * (bottom - top);
}

function clearCanvas(ctx, W, H) {
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = C.bg;
  ctx.fillRect(0, 0, W, H);
}

// ── 画网格 ──────────────────────────────────────────────────
function drawYGrid(ctx, range, top, bottom, left, right, fmt, tickCount = 4) {
  ctx.font = '10px Inter, -apple-system, sans-serif';
  ctx.textAlign  = 'right';
  ctx.textBaseline = 'middle';
  ctx.fillStyle  = C.axis;
  for (let i = 0; i <= tickCount; i++) {
    const frac = i / tickCount;
    const v    = range.min + frac * (range.max - range.min);
    const y    = yVal(v, range, top, bottom);
    ctx.strokeStyle = C.grid;
    ctx.lineWidth   = 1;
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke();
    ctx.fillText(fmt(v), left - 3, y);
  }
  // 零线
  if (range.min < 0 && range.max > 0) {
    const y0 = yVal(0, range, top, bottom);
    ctx.strokeStyle = C.zero;
    ctx.lineWidth   = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(left, y0); ctx.lineTo(right, y0); ctx.stroke();
    ctx.setLineDash([]);
  }
}

// ── Tooltip ─────────────────────────────────────────────────
function attachTooltip(canvas, getNearestPoint, renderHtml) {
  if (canvas._chartTip) canvas._chartTip.remove();
  const tip = document.createElement('div');
  tip.style.cssText = `
    position:absolute; pointer-events:none; display:none;
    background:rgba(15,23,42,.92); color:#f8fafc;
    border-radius:8px; padding:8px 12px; font-size:12px;
    font-family:Inter,-apple-system,sans-serif; line-height:1.65;
    box-shadow:0 4px 20px rgba(0,0,0,.28); z-index:300;
    white-space:nowrap;
  `;
  const wrap = canvas.parentElement;
  if (getComputedStyle(wrap).position === 'static') wrap.style.position = 'relative';
  wrap.appendChild(tip);
  canvas._chartTip = tip;

  canvas.addEventListener('mousemove', e => {
    const rect = canvas.getBoundingClientRect();
    const pt   = getNearestPoint(e.clientX - rect.left, rect.width);
    if (!pt) { tip.style.display = 'none'; return; }
    tip.innerHTML = renderHtml(pt);
    tip.style.display = 'block';
    const tipW = tip.offsetWidth, tipH = tip.offsetHeight;
    let tx = e.clientX - rect.left + 14;
    let ty = e.clientY - rect.top  - tipH / 2;
    if (tx + tipW > rect.width)  tx = e.clientX - rect.left - tipW - 14;
    if (ty < 4) ty = 4;
    tip.style.left = tx + 'px';
    tip.style.top  = ty + 'px';
  });
  canvas.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//  1. 盘中分时缩略图（首页 sparkline，无标签）
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

export function drawIntradaySparkline(canvas, points) {
  const { ctx, W, H } = calcLayout(canvas);
  ctx.clearRect(0, 0, W, H);

  // 映射到交易分钟索引
  const mapped = (points || [])
    .map(pt => ({ idx: tradingMinIdx(pt.t), pct: pt.pct }))
    .filter(p => p.idx !== null);

  if (mapped.length < 2) {
    // 无数据：画灰色中线 + "待行情"
    ctx.strokeStyle = '#dde3ef';
    ctx.lineWidth   = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(3, H / 2); ctx.lineTo(W - 3, H / 2); ctx.stroke();
    ctx.setLineDash([]);
    ctx.font        = `${Math.max(8, H * 0.22)}px Inter,sans-serif`;
    ctx.textAlign   = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle   = '#cbd5e1';
    ctx.fillText('待行情', W / 2, H / 2);
    return;
  }

  const PAD   = { t: 4, b: 4, l: 2, r: 2 };
  const left  = PAD.l, right = W - PAD.r;
  const top   = PAD.t, bottom = H - PAD.b;
  // 小 sparkline 不留午休间隔
  const lunchGapPx = 0;

  const range = computeRange(mapped.map(p => p.pct), 0.1);
  const lastPct  = mapped[mapped.length - 1].pct;
  const lineColor = lastPct >= 0 ? C.up : C.down;

  // 零线
  const y0 = yVal(0, range, top, bottom);
  ctx.strokeStyle = '#e2e8f0';
  ctx.lineWidth   = 0.8;
  ctx.setLineDash([2, 2]);
  ctx.beginPath(); ctx.moveTo(left, y0); ctx.lineTo(right, y0); ctx.stroke();
  ctx.setLineDash([]);

  // 填充渐变
  const grad = ctx.createLinearGradient(0, top, 0, bottom);
  const col  = lastPct >= 0 ? '230,57,70' : '22,163,74';
  grad.addColorStop(0, `rgba(${col},.25)`);
  grad.addColorStop(1, `rgba(${col},.0)`);

  ctx.beginPath();
  let started = false, prevInMorning = null;
  const firstPt = mapped[0];
  const firstX  = tradingIdxToX(firstPt.idx, left, right, lunchGapPx);
  for (let i = 0; i < mapped.length; i++) {
    const p = mapped[i];
    const x = tradingIdxToX(p.idx, left, right, lunchGapPx);
    const y = yVal(p.pct, range, top, bottom);
    const inMorning = p.idx < MORNING_MINS;
    if (prevInMorning !== null && prevInMorning && !inMorning) started = false;
    if (!started) { ctx.moveTo(x, y); started = true; }
    else ctx.lineTo(x, y);
    prevInMorning = inMorning;
  }
  // 闭合路径到零线做填充
  const lastMapped = mapped[mapped.length - 1];
  const lastX = tradingIdxToX(lastMapped.idx, left, right, lunchGapPx);
  ctx.lineTo(lastX, y0);
  ctx.lineTo(firstX, y0);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // 折线
  ctx.strokeStyle = lineColor;
  ctx.lineWidth   = 2;
  ctx.lineJoin    = 'round';
  ctx.lineCap     = 'round';
  ctx.beginPath();
  started = false; prevInMorning = null;
  for (const p of mapped) {
    const x = tradingIdxToX(p.idx, left, right, lunchGapPx);
    const y = yVal(p.pct, range, top, bottom);
    const inMorning = p.idx < MORNING_MINS;
    if (prevInMorning !== null && prevInMorning && !inMorning) started = false;
    if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
    prevInMorning = inMorning;
  }
  ctx.stroke();

  // 末端圆点
  ctx.fillStyle = lineColor;
  ctx.beginPath();
  ctx.arc(lastX, yVal(lastMapped.pct, range, top, bottom), 2.5, 0, Math.PI * 2);
  ctx.fill();
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//  2. 盘中分时完整大图（详情页，含轴标签 + Tooltip）
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

export function drawIntradayChart(canvas, points, mode = 'pct', holdingAmount = 0) {
  const { ctx, W, H } = calcLayout(canvas);
  clearCanvas(ctx, W, H);

  const mapped = (points || [])
    .map(pt => {
      const idx = tradingMinIdx(pt.t);
      return idx !== null ? { idx, t: pt.t, pct: pt.pct, profit: pt.profit } : null;
    })
    .filter(Boolean);

  if (mapped.length === 0) {
    ctx.font = '13px Inter,sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillStyle = C.axis;
    ctx.fillText('盘中数据将在页面刷新时自动积累', W / 2, H / 2);
    return;
  }

  const MARGIN  = { top: 22, bottom: 32, left: 58, right: 14 };
  const top     = MARGIN.top, bottom = H - MARGIN.bottom;
  const left    = MARGIN.left, right  = W - MARGIN.right;
  const LUNCH_W = Math.max(8, (right - left) * 0.04);   // 午休视觉间隔

  const key     = mode === 'pct' ? 'pct' : 'profit';
  const fmt     = mode === 'pct' ? fmtPct : fmtAmt;
  const vals    = mapped.map(p => p[key]).filter(v => v != null);
  const range   = computeRange(vals);

  // Y轴网格
  drawYGrid(ctx, range, top, bottom, left, right, fmt, 4);

  // X轴时间标签
  const xLabels = [
    ['09:30', MORNING_START_MIN - MORNING_START_MIN],
    ['10:00', 30],
    ['10:30', 60],
    ['11:00', 90],
    ['11:30', MORNING_MINS],
    ['13:00', MORNING_MINS],  // 下午起点（右侧）
    ['13:30', MORNING_MINS + 30],
    ['14:00', MORNING_MINS + 60],
    ['14:30', MORNING_MINS + 90],
    ['15:00', MORNING_MINS + 120],
  ];
  ctx.font = '9px Inter,sans-serif';
  ctx.textAlign    = 'center';
  ctx.textBaseline = 'top';
  ctx.fillStyle    = C.axis;
  for (const [label, idx] of xLabels) {
    const x = tradingIdxToX(idx, left, right, LUNCH_W);
    ctx.fillText(label, x, bottom + 4);
  }

  // 午休分割线
  const lunchX = tradingIdxToX(MORNING_MINS, left, right, LUNCH_W) + LUNCH_W / 2;
  ctx.strokeStyle = C.lunch;
  ctx.lineWidth   = 1;
  ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(lunchX, top); ctx.lineTo(lunchX, bottom); ctx.stroke();
  ctx.setLineDash([]);
  ctx.font = '9px Inter,sans-serif';
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillStyle = '#c1cad8';
  ctx.fillText('午', lunchX, (top + bottom) / 2);

  // 确定线色
  const lastVal  = vals[vals.length - 1] ?? 0;
  const lineColor = lastVal >= 0 ? C.up : C.down;

  // 渐变填充
  const grad = ctx.createLinearGradient(0, top, 0, bottom);
  const col   = lastVal >= 0 ? '230,57,70' : '22,163,74';
  grad.addColorStop(0,   `rgba(${col},.22)`);
  grad.addColorStop(0.7, `rgba(${col},.05)`);
  grad.addColorStop(1,   `rgba(${col},.0)`);

  const y0 = yVal(0, range, top, bottom);

  function buildPath(close) {
    ctx.beginPath();
    let started = false, prevMorning = null;
    let firstX = null;
    for (const p of mapped) {
      const v = p[key];
      if (v == null) continue;
      const x = tradingIdxToX(p.idx, left, right, LUNCH_W);
      const y = yVal(v, range, top, bottom);
      const inMorning = p.idx < MORNING_MINS;
      if (prevMorning !== null && prevMorning && !inMorning) started = false;
      if (!started) {
        if (close && firstX === null) firstX = x;
        ctx.moveTo(x, y); started = true;
      } else {
        ctx.lineTo(x, y);
      }
      prevMorning = inMorning;
    }
    if (close && firstX !== null) {
      const lastMapped = mapped.filter(p => p[key] != null).at(-1);
      const lastX = tradingIdxToX(lastMapped.idx, left, right, LUNCH_W);
      ctx.lineTo(lastX, y0);
      ctx.lineTo(firstX, y0);
      ctx.closePath();
    }
  }

  buildPath(true);
  ctx.fillStyle = grad;
  ctx.fill();

  buildPath(false);
  ctx.strokeStyle = lineColor;
  ctx.lineWidth   = 2;
  ctx.lineJoin    = 'round';
  ctx.lineCap     = 'round';
  ctx.stroke();

  // 末端圆点
  const lastMapped = mapped.filter(p => p[key] != null).at(-1);
  if (lastMapped) {
    const lx = tradingIdxToX(lastMapped.idx, left, right, LUNCH_W);
    const ly = yVal(lastMapped[key], range, top, bottom);
    ctx.fillStyle = lineColor;
    ctx.beginPath(); ctx.arc(lx, ly, 3.5, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#fff';
    ctx.beginPath(); ctx.arc(lx, ly, 1.5, 0, Math.PI * 2); ctx.fill();
  }

  // Tooltip
  attachTooltip(
    canvas,
    (mouseX, canvasWidth) => {
      // 找最近的有效数据点（用 tradingIdxToX 反向查找）
      let best = null, bestDist = Infinity;
      for (const p of mapped) {
        if (p[key] == null) continue;
        const px = tradingIdxToX(p.idx, left, right, LUNCH_W);
        const d  = Math.abs(mouseX - px);
        if (d < bestDist) { bestDist = d; best = p; }
      }
      return bestDist < 40 ? best : null;
    },
    pt => {
      const v = pt[key];
      const color = v >= 0 ? '#f87171' : '#4ade80';
      return `<b>${pt.t.slice(0, 5)}</b><br>
${mode === 'pct' ? '估值涨跌' : '估算收益'}：<span style="color:${color}">${fmt(v)}</span>`;
    }
  );
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//  3. 近30日折线图（详情页第二 tab）
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function drawLine30(ctx, points, key, range, top, bottom, left, right, color, lw, dashed) {
  const n = points.length;
  ctx.strokeStyle = color;
  ctx.lineWidth   = lw;
  ctx.lineJoin    = 'round';
  ctx.lineCap     = 'round';
  if (dashed) ctx.setLineDash([5, 4]);
  ctx.beginPath();
  let started = false;
  for (let i = 0; i < n; i++) {
    const v = points[i][key];
    if (v == null) { started = false; continue; }
    const x = left + (i / Math.max(n - 1, 1)) * (right - left);
    const y = yVal(v, range, top, bottom);
    if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
  }
  ctx.stroke();
  ctx.setLineDash([]);
}

export function drawFundChart(canvas, points, mode = 'pct') {
  if (!points || !points.length) return;

  const { ctx, W, H } = calcLayout(canvas);
  const MARGIN = { top: 24, bottom: 28, left: 58, right: 14 };
  const top = MARGIN.top, bottom = H - MARGIN.bottom;
  const left = MARGIN.left, right  = W - MARGIN.right;

  const actualKey = mode === 'pct' ? 'actual'   : 'actual_amount';
  const estKey    = mode === 'pct' ? 'estimate' : 'estimate_amount';
  const fmt       = mode === 'pct' ? fmtPct : fmtAmt;

  clearCanvas(ctx, W, H);

  const allVals = [...points.map(p => p[actualKey]), ...points.map(p => p[estKey])];
  const range   = computeRange(allVals.filter(v => v != null), 0.12);

  drawYGrid(ctx, range, top, bottom, left, right, fmt, 4);

  // X轴日期标签（最多8个）
  const n = points.length;
  const step = Math.max(1, Math.floor(n / 8));
  ctx.font = '10px Inter,sans-serif';
  ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.fillStyle = C.axis;
  for (let i = 0; i < n; i += step) {
    const x = left + (i / Math.max(n - 1, 1)) * (right - left);
    const d = points[i].date?.slice(5) ?? '';
    ctx.fillText(d, x, bottom + 3);
  }

  // 估值线（蓝色虚线）
  drawLine30(ctx, points, estKey, range, top, bottom, left, right, C.estimate, 1.8, true);

  // 实际线（红/绿实线）
  const lastActual = points.map(p => p[actualKey]).filter(v => v != null).at(-1) ?? 0;
  const lineColor  = lastActual >= 0 ? C.up : C.down;
  drawLine30(ctx, points, actualKey, range, top, bottom, left, right, lineColor, 2.5, false);

  // 图例
  const legendItems = [
    { color: lineColor,  label: '实际涨跌', dashed: false },
    { color: C.estimate, label: '事前估值', dashed: true  },
  ];
  ctx.font = '11px Inter,sans-serif';
  ctx.textBaseline = 'middle';
  let lx = left;
  for (const item of legendItems) {
    ctx.strokeStyle = item.color;
    ctx.lineWidth = 2;
    ctx.setLineDash(item.dashed ? [5, 4] : []);
    ctx.beginPath(); ctx.moveTo(lx, top - 10); ctx.lineTo(lx + 16, top - 10); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#64748b'; ctx.textAlign = 'left';
    ctx.fillText(item.label, lx + 20, top - 10);
    lx += ctx.measureText(item.label).width + 44;
  }

  // Tooltip
  attachTooltip(
    canvas,
    (mouseX) => {
      const frac = Math.max(0, Math.min(1, (mouseX - left) / (right - left)));
      const idx  = Math.round(frac * (n - 1));
      return points[idx] ?? null;
    },
    pt => {
      const av = pt[actualKey], ev = pt[estKey];
      const diff = av != null && ev != null ? av - ev : null;
      const diffColor = diff != null ? (Math.abs(diff) < 0.5 ? '#4ade80' : '#fbbf24') : 'inherit';
      return `<b>${pt.date}</b><br>
实际：${av != null ? fmt(av) : '--'}<br>
估值：${ev != null ? fmt(ev) : '--'}<br>
误差：${diff != null ? `<span style="color:${diffColor}">${fmt(diff)}</span>` : '--'}`;
    }
  );
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
//  4. 组合近30日柱状图（首页展开区）
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

export function drawPortfolioChart(canvas, points, mode = 'pct', totalHolding = 0) {
  if (!points || !points.length) return;

  const { ctx, W, H } = calcLayout(canvas);
  const MARGIN = { top: 22, bottom: 28, left: 60, right: 14 };
  const top = MARGIN.top, bottom = H - MARGIN.bottom;
  const left = MARGIN.left, right  = W - MARGIN.right;

  const key  = mode === 'pct' ? 'total_profit_pct' : 'total_profit';
  const fmt  = mode === 'pct' ? fmtPct : fmtAmt;
  const vals = points.map(p => p[key]).filter(v => v != null);
  const range = computeRange(vals, 0.15);

  clearCanvas(ctx, W, H);
  drawYGrid(ctx, range, top, bottom, left, right, fmt, 4);

  // 柱状图
  const n    = points.length;
  const barW = Math.max(3, Math.min(18, (right - left) / n * 0.55));
  const y0   = yVal(0, range, top, bottom);

  for (let i = 0; i < n; i++) {
    const v = points[i][key];
    if (v == null) continue;
    const x = left + (i / Math.max(n - 1, 1)) * (right - left);
    const y = yVal(v, range, top, bottom);
    ctx.fillStyle = v >= 0 ? C.up : C.down;
    if (v >= 0) ctx.fillRect(x - barW / 2, y, barW, y0 - y);
    else        ctx.fillRect(x - barW / 2, y0, barW, y - y0);
  }

  // X轴日期
  const step = Math.max(1, Math.floor(n / 8));
  ctx.font = '10px Inter,sans-serif';
  ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.fillStyle = C.axis;
  for (let i = 0; i < n; i += step) {
    const x = left + (i / Math.max(n - 1, 1)) * (right - left);
    ctx.fillText(points[i].date?.slice(5) ?? '', x, bottom + 3);
  }

  // Tooltip
  attachTooltip(
    canvas,
    (mouseX) => {
      const frac = Math.max(0, Math.min(1, (mouseX - left) / (right - left)));
      const idx  = Math.round(frac * (n - 1));
      return points[idx] ?? null;
    },
    pt => {
      const v = pt[key];
      return `<b>${pt.date}</b><br>${mode === 'pct' ? '日收益率' : '日收益'}：<span style="color:${v >= 0 ? '#f87171' : '#4ade80'}">${fmt(v)}</span>`;
    }
  );
}
