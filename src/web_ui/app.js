const VIEW_TITLES = {
  dashboard: "研究总览",
  asset: "标的研究",
  portfolio: "组合与持仓",
  alerts: "提醒中心",
  reports: "报告中心",
  ai: "AI 研究",
  system: "系统状态"
};

const state = {
  userId: null,
  users: [],
  selectedAssetId: null,
  selectedAiTaskId: null,
  assets: new Map(),
  health: null,
  watchlist: [],
  holdings: [],
  reports: [],
  alerts: [],
  alertRules: [],
  snapshots: [],
  aiTasks: [],
  qualityRuns: [],
  unresolved: [],
  marketData: null
};

const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");
const emptyState = (message) => `<div class="empty-state">${escapeHtml(message)}</div>`;
const badge = (value) => `<span class="badge ${escapeHtml(value)}">${escapeHtml(value)}</span>`;

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  });
}

function formatPercent(value, digits = 1) {
  if (value === null || value === undefined) return "—";
  return `${formatNumber(Number(value) * 100, digits)}%`;
}

function formatTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString("zh-CN");
}

function safeUrl(value) {
  return /^https?:\/\//i.test(String(value || "")) ? String(value) : null;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.userId) headers["X-User-Id"] = state.userId;
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = Array.isArray(payload.detail)
      ? payload.detail.map((item) => item.msg || JSON.stringify(item)).join("；")
      : payload.detail;
    throw new Error(detail || `${response.status} ${response.statusText}`);
  }
  return response.status === 204 ? null : response.json();
}

let noticeTimer = null;
function showNotice(message, isError = false) {
  const element = $("notice");
  element.textContent = message;
  element.className = `notice${isError ? " error" : ""}`;
  element.style.display = "block";
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => {
    element.style.display = "none";
  }, isError ? 7000 : 3500);
}

function setView(viewName) {
  const name = VIEW_TITLES[viewName] ? viewName : "dashboard";
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.dataset.view === name);
  });
  document.querySelectorAll(".nav-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.viewTarget === name);
  });
  $("view-title").textContent = VIEW_TITLES[name];
  history.replaceState(null, "", `#${name}`);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function assetLabel(assetId) {
  const asset = state.assets.get(assetId);
  return asset
    ? `${asset.display_name || asset.standard_symbol}（${asset.standard_symbol}）`
    : assetId;
}

function requireSelectedAsset() {
  if (!state.selectedAssetId) throw new Error("请先搜索并选择研究标的");
  return state.selectedAssetId;
}

async function ensureAsset(assetId) {
  if (!assetId || state.assets.has(assetId)) return;
  const asset = await api(`/assets/${encodeURIComponent(assetId)}`);
  state.assets.set(asset.asset_id, asset);
}

async function hydrateAssetLabels() {
  const ids = new Set();
  [
    ...state.watchlist,
    ...state.holdings,
    ...state.reports,
    ...state.alerts,
    ...state.aiTasks
  ].forEach((item) => {
    if (item.asset_id) ids.add(item.asset_id);
  });
  await Promise.all([...ids].map((assetId) => ensureAsset(assetId)));
}

function updateSelectedAssetLabels() {
  const text = state.selectedAssetId
    ? assetLabel(state.selectedAssetId)
    : "未选择标的";
  document.querySelectorAll("[data-selected-asset-label]").forEach((element) => {
    element.textContent = text;
  });
}

function renderAssetOptions(assets, preferredAssetId = null) {
  const select = $("asset-select");
  select.innerHTML = assets.length
    ? assets.map((asset) =>
      `<option value="${escapeHtml(asset.asset_id)}">${escapeHtml(assetLabel(asset.asset_id))}</option>`
    ).join("")
    : '<option value="">未找到本地标的</option>';
  const preferred = preferredAssetId || state.selectedAssetId;
  if (preferred && assets.some((asset) => asset.asset_id === preferred)) {
    select.value = preferred;
  }
}

async function searchAssets(query = "", openAssetView = false) {
  const assets = await api(`/assets?query=${encodeURIComponent(query)}&limit=100`);
  assets.forEach((asset) => state.assets.set(asset.asset_id, asset));
  renderAssetOptions(assets);
  if (assets.length) await selectAsset($("asset-select").value || assets[0].asset_id);
  if (openAssetView) setView("asset");
  return assets;
}

async function selectAsset(assetId) {
  await ensureAsset(assetId);
  const changed = state.selectedAssetId !== assetId;
  state.selectedAssetId = assetId;
  const select = $("asset-select");
  if (![...select.options].some((option) => option.value === assetId)) {
    const option = document.createElement("option");
    option.value = assetId;
    option.textContent = assetLabel(assetId);
    select.append(option);
  }
  select.value = assetId;
  updateSelectedAssetLabels();
  fillResearchForms();
  if (changed) resetMarketData();
  await loadAssetResearch(assetId);
}

function fillResearchForms() {
  const watchlistItem = state.watchlist.find((item) => item.asset_id === state.selectedAssetId);
  const holding = state.holdings.find((item) => item.asset_id === state.selectedAssetId);
  $("watchlist-reason").value = watchlistItem?.reason || "";
  $("watchlist-tags").value = (watchlistItem?.tags || []).join(", ");
  $("remove-watchlist-button").disabled = !watchlistItem;
  for (const prefix of ["asset-holding", "holding"]) {
    $(`${prefix}-quantity`).value = holding?.quantity ?? "";
    $(`${prefix}-cost`).value = holding?.average_cost ?? "";
    $(`${prefix}-currency`).value = holding?.currency || "CNY";
    $(`${prefix}-note`).value = holding?.note || "";
  }
  $("remove-asset-holding-button").disabled = !holding;
  $("remove-holding-button").disabled = !holding;
}

async function loadAssetResearch(assetId) {
  const [asset, factors, events] = await Promise.all([
    api(`/assets/${encodeURIComponent(assetId)}`),
    api(`/assets/${encodeURIComponent(assetId)}/factors`),
    api(`/assets/${encodeURIComponent(assetId)}/events`)
  ]);
  state.assets.set(asset.asset_id, asset);
  $("market-dataset").value = asset.asset_type === "stock"
    ? "stock_daily_bars"
    : "fund_exchange_daily_bars";
  $("asset-summary").className = "asset-hero";
  $("asset-summary").innerHTML = `
    <div>
      <h3>${escapeHtml(asset.display_name || asset.standard_symbol)}</h3>
      <p>${escapeHtml(asset.asset_type)} · ${escapeHtml(asset.market || "未知市场")} · ${escapeHtml(asset.exchange || "未知交易所")}</p>
    </div>
    <div class="asset-code">${escapeHtml(asset.standard_symbol)}</div>`;
  renderFactors(factors);
  renderEvents(events);
}

function resetMarketData() {
  state.marketData = null;
  $("market-record-count").textContent = "0 条";
  $("market-status").innerHTML = "";
  $("market-chart").className = "market-chart empty-state";
  $("market-chart").textContent = "提交查询后显示 OHLC 与成交量。";
  $("market-table").innerHTML = "";
}

function marketRequestPayload() {
  requireSelectedAsset();
  return {
    dataset: $("market-dataset").value,
    start_date: $("market-start-date").value,
    end_date: $("market-end-date").value,
    allow_remote: $("market-allow-remote").checked,
    allow_stale_on_error: true,
    provider_timeout_seconds: 15
  };
}

function renderMarketData(result) {
  state.marketData = result;
  const data = result.data;
  const quality = result.quality;
  const records = data.records || [];
  $("market-record-count").textContent = `${records.length} 条`;
  const backends = data.metadata?.backends || [];
  const providerErrors = data.metadata?.provider_errors || {};
  const statusParts = [
    badge(data.complete ? "complete" : "incomplete"),
    badge(data.fetched_remote ? "remote" : "local"),
    data.stale ? badge("stale") : "",
    badge(quality.can_publish ? "publishable" : "blocked"),
    `<span class="pill">覆盖 ${escapeHtml(data.coverage_start || "—")} 至 ${escapeHtml(data.coverage_end || "—")}</span>`,
    `<span class="pill">来源 ${escapeHtml((data.sources || []).join(", ") || "无")}</span>`,
    `<span class="pill">后端 ${escapeHtml(backends.join(", ") || "无")}</span>`
  ];
  if (data.error) statusParts.push(`<span class="badge fail">${escapeHtml(data.error)}</span>`);
  if (Object.keys(providerErrors).length) {
    statusParts.push(
      `<span class="badge warn">Provider 错误 ${escapeHtml(JSON.stringify(providerErrors))}</span>`
    );
  }
  $("market-status").innerHTML = statusParts.join("");
  renderMarketChart(records);
  renderMarketTable(records);
  const latest = records[records.length - 1];
  if (latest && state.selectedAssetId) {
    const valuationInput = [...document.querySelectorAll("[data-price-asset]")]
      .find((input) => input.dataset.priceAsset === state.selectedAssetId);
    if (valuationInput && !valuationInput.value) {
      valuationInput.value = latest.close;
    }
    $("observed-price").value = latest.close;
  }
}

function renderMarketChart(records) {
  const normalized = records.map((record) => ({
    date: String(record.trade_date || ""),
    open: Number(record.open),
    high: Number(record.high),
    low: Number(record.low),
    close: Number(record.close),
    volume: Number(record.volume || 0)
  })).filter((record) =>
    record.date
    && [record.open, record.high, record.low, record.close].every(Number.isFinite)
  ).slice(-100);
  if (!normalized.length) {
    $("market-chart").className = "market-chart empty-state";
    $("market-chart").textContent = "查询范围内没有可绘制的日线记录。";
    return;
  }

  const width = 960;
  const height = 330;
  const left = 58;
  const right = 20;
  const top = 18;
  const priceBottom = 235;
  const volumeTop = 255;
  const volumeBottom = 312;
  const highs = normalized.map((item) => item.high);
  const lows = normalized.map((item) => item.low);
  const priceMin = Math.min(...lows);
  const priceMax = Math.max(...highs);
  const priceRange = priceMax - priceMin || 1;
  const volumeMax = Math.max(...normalized.map((item) => item.volume), 1);
  const step = (width - left - right) / normalized.length;
  const candleWidth = Math.max(2, Math.min(10, step * 0.62));
  const xFor = (index) => left + step * index + step / 2;
  const yFor = (value) => top + ((priceMax - value) / priceRange) * (priceBottom - top);
  const grid = Array.from({ length: 5 }, (_, index) => {
    const ratio = index / 4;
    const y = top + ratio * (priceBottom - top);
    const value = priceMax - ratio * priceRange;
    return `
      <line x1="${left}" y1="${y}" x2="${width - right}" y2="${y}" stroke="#e7ece7"/>
      <text x="${left - 8}" y="${y + 4}" text-anchor="end" fill="#68736b" font-size="11">${formatNumber(value, 3)}</text>`;
  }).join("");
  const candles = normalized.map((item, index) => {
    const x = xFor(index);
    const color = item.close >= item.open ? "#c4493d" : "#1d8b62";
    const bodyTop = Math.min(yFor(item.open), yFor(item.close));
    const bodyHeight = Math.max(1, Math.abs(yFor(item.open) - yFor(item.close)));
    const volumeHeight = (item.volume / volumeMax) * (volumeBottom - volumeTop);
    return `
      <g>
        <title>${escapeHtml(item.date)} 开 ${item.open} 高 ${item.high} 低 ${item.low} 收 ${item.close}</title>
        <line x1="${x}" y1="${yFor(item.high)}" x2="${x}" y2="${yFor(item.low)}" stroke="${color}" stroke-width="1"/>
        <rect x="${x - candleWidth / 2}" y="${bodyTop}" width="${candleWidth}" height="${bodyHeight}" fill="${color}"/>
        <rect x="${x - candleWidth / 2}" y="${volumeBottom - volumeHeight}" width="${candleWidth}" height="${volumeHeight}" fill="${color}" opacity=".55"/>
      </g>`;
  }).join("");
  const firstDate = normalized[0].date;
  const lastDate = normalized[normalized.length - 1].date;
  $("market-chart").className = "market-chart";
  $("market-chart").innerHTML = `
    <div class="market-legend">
      <span class="legend-item" style="--legend-color:#c4493d">上涨</span>
      <span class="legend-item" style="--legend-color:#1d8b62">下跌</span>
      <span>最近 ${normalized.length} 个交易日</span>
    </div>
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="OHLC 与成交量图">
      ${grid}
      <line x1="${left}" y1="${volumeBottom}" x2="${width - right}" y2="${volumeBottom}" stroke="#dfe6df"/>
      ${candles}
      <text x="${left}" y="${height - 2}" fill="#68736b" font-size="11">${escapeHtml(firstDate)}</text>
      <text x="${width - right}" y="${height - 2}" text-anchor="end" fill="#68736b" font-size="11">${escapeHtml(lastDate)}</text>
      <text x="${left - 8}" y="${volumeTop + 8}" text-anchor="end" fill="#68736b" font-size="11">量</text>
    </svg>`;
}

function renderMarketTable(records) {
  const latest = records.slice(-12).reverse();
  $("market-table").innerHTML = latest.length
    ? `<table>
        <thead><tr><th>日期</th><th class="numeric">开</th><th class="numeric">高</th><th class="numeric">低</th><th class="numeric">收</th><th class="numeric">成交量</th><th>来源</th></tr></thead>
        <tbody>${latest.map((item) => `
          <tr>
            <td>${escapeHtml(item.trade_date)}</td>
            <td class="numeric">${formatNumber(item.open, 3)}</td>
            <td class="numeric">${formatNumber(item.high, 3)}</td>
            <td class="numeric">${formatNumber(item.low, 3)}</td>
            <td class="numeric">${formatNumber(item.close, 3)}</td>
            <td class="numeric">${formatNumber(item.volume, 0)}</td>
            <td>${escapeHtml(item.source || "—")}</td>
          </tr>`).join("")}
        </tbody>
      </table>`
    : "";
}

function renderFactors(factors) {
  $("factor-summary").textContent = `${factors.length} 项`;
  if (!factors.length) {
    $("factor-chart").className = "chart empty-state";
    $("factor-chart").textContent = "暂无因子数据";
    $("asset-factors").innerHTML = "";
    return;
  }
  const grouped = new Map();
  factors.forEach((item) => {
    if (!grouped.has(item.factor_name)) grouped.set(item.factor_name, []);
    grouped.get(item.factor_name).push(item);
  });
  grouped.forEach((items) => items.sort((a, b) => a.as_of_date.localeCompare(b.as_of_date)));
  const latest = [...grouped.entries()].map(([name, items]) => ({
    name,
    item: items[items.length - 1]
  })).sort((a, b) => a.name.localeCompare(b.name));
  $("asset-factors").innerHTML = `
    <table>
      <thead><tr><th>因子</th><th>日期</th><th class="numeric">数值</th><th>版本</th></tr></thead>
      <tbody>${latest.map(({ name, item }) => `
        <tr>
          <td>${escapeHtml(name)}</td>
          <td>${escapeHtml(item.as_of_date)}</td>
          <td class="numeric">${formatNumber(item.value, 6)}</td>
          <td>${escapeHtml(item.calculation_version)}</td>
        </tr>`).join("")}
      </tbody>
    </table>`;

  const [seriesName, rawSeries] = [...grouped.entries()]
    .sort((a, b) => b[1].length - a[1].length)[0];
  const series = rawSeries.slice(-24);
  const values = series.map((item) => Number(item.value));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const width = 680;
  const height = 170;
  const pad = 20;
  const points = values.map((value, index) => {
    const x = series.length === 1
      ? width / 2
      : pad + (index * (width - pad * 2)) / (series.length - 1);
    const y = max === min
      ? height / 2
      : pad + ((max - value) * (height - pad * 2)) / (max - min);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  $("factor-chart").className = "chart";
  $("factor-chart").innerHTML = `
    <div class="chart-caption">
      <span>${escapeHtml(seriesName)} · 最近 ${series.length} 个值</span>
      <span>${formatNumber(min, 4)} — ${formatNumber(max, 4)}</span>
    </div>
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(seriesName)} 趋势">
      <defs>
        <linearGradient id="factorFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#2b8a5b" stop-opacity=".28"/>
          <stop offset="1" stop-color="#2b8a5b" stop-opacity=".02"/>
        </linearGradient>
      </defs>
      <line x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}" stroke="#dfe6df"/>
      <polyline points="${points}" fill="none" stroke="#1f6b45" stroke-width="3" stroke-linejoin="round"/>
    </svg>`;
}

function renderEvents(events) {
  $("event-summary").textContent = `${events.length} 项`;
  $("asset-events").innerHTML = events.length
    ? events.slice().sort((a, b) => b.event_date.localeCompare(a.event_date)).map((event) => {
      const url = safeUrl(event.source_url);
      return `<article class="timeline-item">
        <div class="meta-row"><span>${escapeHtml(event.event_date)}</span>${badge(event.event_type)}<span>置信度 ${formatPercent(event.confidence)}</span></div>
        <strong>${escapeHtml(event.title)}</strong>
        <p>${escapeHtml(event.summary || "暂无摘要")}</p>
        <div class="meta-row"><span>${escapeHtml(event.source)}</span>${url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">查看来源</a>` : ""}</div>
      </article>`;
    }).join("")
    : emptyState("当前标的暂无结构化事件");
}

async function loadUsers(preferredUserId = null) {
  const users = await api("/users");
  state.users = users;
  $("user-select").innerHTML = users.map((user) =>
    `<option value="${escapeHtml(user.user_id)}">${escapeHtml(user.display_name)}</option>`
  ).join("");
  const preferred = preferredUserId || state.userId;
  if (preferred && users.some((user) => user.user_id === preferred)) {
    $("user-select").value = preferred;
  }
  state.userId = $("user-select").value || null;
}

async function refreshAll() {
  const [
    health,
    watchlist,
    holdings,
    reports,
    alerts,
    alertRules,
    snapshots,
    aiTasks,
    qualityRuns,
    unresolved
  ] = await Promise.all([
    api("/health"),
    api("/watchlist"),
    api("/holdings"),
    api("/reports"),
    api("/alerts"),
    api("/alert-rules"),
    api("/portfolio/snapshots"),
    api("/ai/tasks"),
    api("/data-quality/runs?limit=50"),
    api("/master-data/unresolved")
  ]);
  Object.assign(state, {
    health,
    watchlist,
    holdings,
    reports,
    alerts,
    alertRules,
    snapshots,
    aiTasks,
    qualityRuns,
    unresolved
  });
  await hydrateAssetLabels();
  renderDashboard();
  renderPortfolio();
  renderAlerts();
  renderReports();
  renderAiTasks();
  renderSystem();
  fillResearchForms();
  if (!state.selectedAssetId) {
    const firstAssetId = watchlist[0]?.asset_id || holdings[0]?.asset_id;
    if (firstAssetId) await selectAsset(firstAssetId);
  }
}

function renderDashboard() {
  const pendingAlerts = state.alerts.filter((item) => item.status === "pending");
  $("metric-watchlist").textContent = state.watchlist.length;
  $("metric-holdings").textContent = state.holdings.length;
  $("metric-alerts").textContent = pendingAlerts.length;
  $("metric-reports").textContent = state.reports.length;
  $("metric-ai").textContent = state.aiTasks.length;

  $("dashboard-watchlist").innerHTML = state.watchlist.length
    ? state.watchlist.slice(0, 6).map((item) => `
      <article class="list-card">
        <div class="list-card-main">
          <button class="asset-button" data-asset="${escapeHtml(item.asset_id)}">${escapeHtml(assetLabel(item.asset_id))}</button>
          <p>${escapeHtml(item.reason || "未填写关注理由")}</p>
          <div class="meta-row">${(item.tags || []).map((tag) => `<span class="pill">${escapeHtml(tag)}</span>`).join("")}</div>
        </div>
      </article>`).join("")
    : emptyState("自选为空，可在标的研究中添加");

  $("dashboard-alerts").innerHTML = pendingAlerts.length
    ? pendingAlerts.slice(0, 5).map(alertCard).join("")
    : emptyState("当前没有待处理提醒");

  const latestSnapshot = state.snapshots[0];
  $("dashboard-portfolio").innerHTML = latestSnapshot
    ? `<div class="snapshot-summary">
        ${snapshotMetric("估值日期", latestSnapshot.as_of_date)}
        ${snapshotMetric("总市值", formatNumber(latestSnapshot.total_market_value))}
        ${snapshotMetric("浮动盈亏", formatNumber(latestSnapshot.unrealized_pnl))}
        ${snapshotMetric("最大权重", formatPercent(latestSnapshot.max_weight))}
        ${snapshotMetric("HHI", formatNumber(latestSnapshot.concentration_hhi, 4))}
      </div>
      <button data-snapshot="${escapeHtml(latestSnapshot.snapshot_id)}">查看持仓明细</button>`
    : emptyState("暂无组合估值快照");

  $("dashboard-reports").innerHTML = state.reports.length
    ? state.reports.slice(0, 5).map(reportCard).join("")
    : emptyState("尚未创建分析报告");

  $("dashboard-ai").innerHTML = state.aiTasks.length
    ? state.aiTasks.slice(0, 5).map(aiTaskCard).join("")
    : emptyState("尚未创建 AI 研究任务");
}

function renderPortfolio() {
  $("holding-count").textContent = `${state.holdings.length} 项`;
  $("snapshot-count").textContent = `${state.snapshots.length} 个`;
  $("portfolio-holdings").innerHTML = state.holdings.length
    ? state.holdings.map((holding) => `
      <article class="list-card">
        <div class="list-card-main">
          <button class="asset-button" data-asset="${escapeHtml(holding.asset_id)}">${escapeHtml(assetLabel(holding.asset_id))}</button>
          <p>数量 ${formatNumber(holding.quantity, 4)} · 成本 ${formatNumber(holding.average_cost, 4)} ${escapeHtml(holding.currency)}</p>
          <div class="meta-row"><span>${escapeHtml(holding.note || "无备注")}</span><span>更新 ${formatTime(holding.updated_at)}</span></div>
        </div>
        <div class="actions">
          <button data-edit-holding="${escapeHtml(holding.asset_id)}">编辑</button>
          <button class="danger" data-remove-holding="${escapeHtml(holding.asset_id)}">删除</button>
        </div>
      </article>`).join("")
    : emptyState("当前没有持仓");

  $("valuation-price-fields").innerHTML = state.holdings.length
    ? state.holdings.map((holding) => `
      <label>${escapeHtml(assetLabel(holding.asset_id))}
        <input type="number" step="any" min="0" required data-price-asset="${escapeHtml(holding.asset_id)}" placeholder="当前价格">
      </label>`).join("")
    : emptyState("先添加持仓后再生成估值");
  $("valuation-form").querySelector('button[type="submit"]').disabled = !state.holdings.length;

  $("portfolio-snapshots").innerHTML = state.snapshots.length
    ? state.snapshots.map((snapshot) => `
      <article class="list-card">
        <div class="list-card-main">
          <strong>${escapeHtml(snapshot.as_of_date)}</strong>
          <p>市值 ${formatNumber(snapshot.total_market_value)} · 盈亏 ${formatNumber(snapshot.unrealized_pnl)}</p>
          <div class="meta-row"><span>最大权重 ${formatPercent(snapshot.max_weight)}</span><span>HHI ${formatNumber(snapshot.concentration_hhi, 4)}</span></div>
        </div>
        <div class="actions"><button data-snapshot="${escapeHtml(snapshot.snapshot_id)}">查看</button></div>
      </article>`).join("")
    : emptyState("暂无组合快照");
}

function snapshotMetric(label, value) {
  return `<div class="snapshot-metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

async function loadSnapshot(snapshotId) {
  const valuation = await api(`/portfolio/snapshots/${encodeURIComponent(snapshotId)}`);
  const snapshot = valuation.snapshot;
  $("portfolio-detail").className = "";
  $("portfolio-detail").innerHTML = `
    <div class="snapshot-summary">
      ${snapshotMetric("日期", snapshot.as_of_date)}
      ${snapshotMetric("总市值", formatNumber(snapshot.total_market_value))}
      ${snapshotMetric("总成本", formatNumber(snapshot.total_cost_value))}
      ${snapshotMetric("浮动盈亏", formatNumber(snapshot.unrealized_pnl))}
      ${snapshotMetric("最大权重", formatPercent(snapshot.max_weight))}
    </div>
    <div class="data-table-wrap">
      <table>
        <thead><tr><th>标的</th><th class="numeric">数量</th><th class="numeric">价格</th><th class="numeric">市值</th><th class="numeric">浮盈亏</th><th class="numeric">权重</th></tr></thead>
        <tbody>${valuation.positions.map((position) => `
          <tr>
            <td><button class="asset-button" data-asset="${escapeHtml(position.asset_id)}">${escapeHtml(assetLabel(position.asset_id))}</button></td>
            <td class="numeric">${formatNumber(position.quantity, 4)}</td>
            <td class="numeric">${formatNumber(position.market_price, 4)}</td>
            <td class="numeric">${formatNumber(position.market_value)}</td>
            <td class="numeric">${formatNumber(position.unrealized_pnl)}</td>
            <td class="numeric">${formatPercent(position.weight)}</td>
          </tr>`).join("")}
        </tbody>
      </table>
    </div>`;
  setView("portfolio");
}

function alertCard(alert) {
  return `<article class="list-card">
    <div class="list-card-main">
      <strong>${escapeHtml(alert.title)}</strong>
      <p>${escapeHtml(alert.message)}</p>
      <div class="meta-row">${badge(alert.status)}<span>${formatTime(alert.triggered_at)}</span>${alert.asset_id ? `<span>${escapeHtml(assetLabel(alert.asset_id))}</span>` : ""}</div>
    </div>
    <div class="actions">${alert.status === "pending" ? `<button data-alert-ack="${escapeHtml(alert.alert_id)}">确认</button>` : ""}</div>
  </article>`;
}

function renderAlerts() {
  const filter = $("alert-filter").value;
  const alerts = filter ? state.alerts.filter((item) => item.status === filter) : state.alerts;
  $("alerts-list").innerHTML = alerts.length
    ? alerts.map(alertCard).join("")
    : emptyState("当前筛选条件下没有提醒");
  $("rule-count").textContent = `${state.alertRules.length} 条`;
  $("alert-rules").innerHTML = state.alertRules.length
    ? state.alertRules.map((rule) => `
      <article class="list-card">
        <div class="list-card-main">
          <strong>${escapeHtml(rule.rule_type)}</strong>
          <p>${escapeHtml(rule.asset_id ? assetLabel(rule.asset_id) : "全部标的 / 组合")}</p>
          <div class="meta-row"><span>${escapeHtml(JSON.stringify(rule.config))}</span>${badge(rule.enabled ? "enabled" : "disabled")}</div>
        </div>
      </article>`).join("")
    : emptyState("尚未创建提醒规则");
}

function reportCard(report) {
  return `<article class="list-card">
    <div class="list-card-main">
      <button class="text-button" data-report="${escapeHtml(report.report_id)}">${escapeHtml(report.title)}</button>
      <p>${escapeHtml(report.summary || "无摘要")}</p>
      <div class="meta-row">${badge(report.status)}<span>${escapeHtml(assetLabel(report.asset_id))}</span><span>${formatTime(report.updated_at)}</span></div>
    </div>
  </article>`;
}

function renderReports() {
  $("report-count").textContent = `${state.reports.length} 篇`;
  $("reports-list").innerHTML = state.reports.length
    ? state.reports.map(reportCard).join("")
    : emptyState("尚未创建分析报告");
}

async function loadReport(reportId) {
  const report = await api(`/reports/${encodeURIComponent(reportId)}`);
  $("report-detail").className = "document-view";
  $("report-detail").innerHTML = `
    <header>
      <h3>${escapeHtml(report.title)}</h3>
      <p class="muted">${escapeHtml(assetLabel(report.asset_id))} · ${escapeHtml(report.status)} · ${formatTime(report.updated_at)}</p>
      <p>${escapeHtml(report.summary || "")}</p>
    </header>
    <pre class="document-content">${escapeHtml(report.content_markdown)}</pre>`;
  setView("reports");
}

function aiTaskCard(task) {
  return `<article class="list-card">
    <div class="list-card-main">
      <button class="text-button" data-ai-task="${escapeHtml(task.task_id)}">${escapeHtml(task.question)}</button>
      <div class="meta-row">${badge(task.status)}<span>${escapeHtml(assetLabel(task.asset_id))}</span><span>${formatTime(task.created_at)}</span></div>
    </div>
  </article>`;
}

function renderAiTasks() {
  $("ai-task-count").textContent = `${state.aiTasks.length} 项`;
  $("ai-tasks").innerHTML = state.aiTasks.length
    ? state.aiTasks.map(aiTaskCard).join("")
    : emptyState("尚未创建 AI 研究任务");
}

async function loadAiTask(taskId) {
  const [task, evidence, tools] = await Promise.all([
    api(`/ai/tasks/${encodeURIComponent(taskId)}`),
    api(`/ai/tasks/${encodeURIComponent(taskId)}/evidence`),
    api(`/ai/tasks/${encodeURIComponent(taskId)}/tools`)
  ]);
  state.selectedAiTaskId = taskId;
  $("execute-ai-button").disabled = task.status === "completed";
  const claims = task.result?.claims || [];
  $("ai-detail").className = "";
  $("ai-detail").innerHTML = `
    <div class="meta-row">${badge(task.status)}<span>${escapeHtml(assetLabel(task.asset_id))}</span><span>模型 ${escapeHtml(task.model_name || "未执行")}</span></div>
    <h3>${escapeHtml(task.question)}</h3>
    ${task.error ? `<div class="empty-state">${escapeHtml(task.error)}</div>` : ""}
    <div class="document-content">${escapeHtml(task.result?.content || "上下文已冻结，尚未执行模型。")}</div>
    <div class="claim-list">${claims.map((claim) => `
      <div class="claim ${escapeHtml(claim.claim_type)}">
        <strong>${escapeHtml(claim.claim_type)}</strong>
        <div>${escapeHtml(claim.text)}</div>
        <div class="meta-row"><span>证据 ${escapeHtml((claim.evidence_ids || []).join(", ") || "无")}</span></div>
      </div>`).join("")}</div>`;
  $("ai-evidence").innerHTML = evidence.length
    ? evidence.map((item) => `
      <article class="list-card">
        <div class="list-card-main">
          <strong>${escapeHtml(item.title)}</strong>
          <p>${escapeHtml(item.content)}</p>
          <div class="meta-row">${badge(item.evidence_type)}<span>${escapeHtml(item.evidence_id)}</span></div>
        </div>
      </article>`).join("")
    : emptyState("任务没有证据");
  $("ai-tools").innerHTML = tools.length
    ? tools.map((tool) => `
      <article class="list-card">
        <div class="list-card-main">
          <strong>${escapeHtml(tool.tool_name)}</strong>
          <p>${escapeHtml(JSON.stringify(tool.arguments))}</p>
          <div class="meta-row">${badge(tool.status)}${tool.error ? `<span>${escapeHtml(tool.error)}</span>` : ""}</div>
        </div>
      </article>`).join("")
    : emptyState("任务没有工具调用记录");
  setView("ai");
}

function renderSystem() {
  $("system-health").className = "";
  $("system-health").innerHTML = state.health
    ? `<div class="snapshot-summary">
        ${snapshotMetric("API", state.health.status)}
        ${snapshotMetric("默认用户", state.health.default_user_id)}
      </div>`
    : emptyState("无法读取服务状态");
  $("users-list").innerHTML = state.users.length
    ? state.users.map((user) => `
      <article class="list-card">
        <div class="list-card-main"><strong>${escapeHtml(user.display_name)}</strong><p>${escapeHtml(user.user_id)}</p></div>
        ${user.user_id === state.userId ? badge("current") : ""}
      </article>`).join("")
    : emptyState("没有用户空间");
  $("quality-count").textContent = `${state.qualityRuns.length} 项`;
  $("quality-runs").innerHTML = state.qualityRuns.length
    ? state.qualityRuns.map((run) => `
      <article class="list-card">
        <div class="list-card-main">
          <button class="text-button" data-quality-run="${escapeHtml(run.run_id)}">${escapeHtml(run.task_name)}</button>
          <p>${escapeHtml(run.dataset || "未指定数据集")} · ${escapeHtml(run.source || "未指定来源")}</p>
          <div class="meta-row">${badge(run.status)}<span>${formatTime(run.started_at)}</span><span>${run.record_count ?? "—"} 条</span></div>
        </div>
      </article>`).join("")
    : emptyState("暂无结构化数据任务记录");
  $("unresolved-count").textContent = `${state.unresolved.length} 项`;
  $("unresolved-observations").innerHTML = state.unresolved.length
    ? `<table>
        <thead><tr><th>来源</th><th>原始代码</th><th>名称</th><th>状态</th><th>原因</th></tr></thead>
        <tbody>${state.unresolved.map((item) => `
          <tr>
            <td>${escapeHtml(item.observation?.source)}</td>
            <td>${escapeHtml(item.observation?.raw_symbol)}</td>
            <td>${escapeHtml(item.observation?.name || "—")}</td>
            <td>${escapeHtml(item.resolution_status)}</td>
            <td>${escapeHtml(item.reason || "—")}</td>
          </tr>`).join("")}
        </tbody>
      </table>`
    : emptyState("没有待处理主数据观察");
}

async function loadQualityRun(runId) {
  const summary = await api(`/data-quality/runs/${encodeURIComponent(runId)}`);
  $("quality-detail").className = "";
  $("quality-detail").innerHTML = `
    <div class="meta-row">${badge(summary.run.status)}${badge(summary.can_publish ? "publishable" : "blocked")}</div>
    <h3>${escapeHtml(summary.run.task_name)}</h3>
    <p class="muted">通过 ${summary.pass_count} · 警告 ${summary.warn_count} · 失败 ${summary.fail_count}</p>
    <div class="card-list">${summary.checks.length
      ? summary.checks.map((check) => `
        <article class="list-card">
          <div class="list-card-main">
            <strong>${escapeHtml(check.check_name)}</strong>
            <p>${escapeHtml(check.message)}</p>
            <div class="meta-row">${badge(check.level)}<span>${escapeHtml(JSON.stringify(check.metrics))}</span></div>
          </div>
        </article>`).join("")
      : emptyState("该任务没有质量检查结果")}
    </div>`;
  setView("system");
}

async function saveHolding(prefix) {
  const assetId = requireSelectedAsset();
  const cost = $(`${prefix}-cost`).value;
  await api(`/holdings/${encodeURIComponent(assetId)}`, {
    method: "PUT",
    body: JSON.stringify({
      quantity: Number($(`${prefix}-quantity`).value),
      average_cost: cost === "" ? null : Number(cost),
      currency: $(`${prefix}-currency`).value,
      note: $(`${prefix}-note`).value
    })
  });
  showNotice("当前持仓已保存");
  await refreshAll();
}

async function removeHolding(assetId) {
  if (!window.confirm(`确认删除 ${assetLabel(assetId)} 的当前持仓？`)) return;
  await api(`/holdings/${encodeURIComponent(assetId)}`, { method: "DELETE" });
  showNotice("当前持仓已删除");
  await refreshAll();
}

async function handleDelegatedClick(event) {
  const target = event.target.closest("button");
  if (!target) return;
  if (target.dataset.openView) {
    setView(target.dataset.openView);
  } else if (target.dataset.asset) {
    await selectAsset(target.dataset.asset);
    setView("asset");
  } else if (target.dataset.editHolding) {
    await selectAsset(target.dataset.editHolding);
    setView("portfolio");
  } else if (target.dataset.removeHolding) {
    await removeHolding(target.dataset.removeHolding);
  } else if (target.dataset.snapshot) {
    await loadSnapshot(target.dataset.snapshot);
  } else if (target.dataset.alertAck) {
    await api(`/alerts/${encodeURIComponent(target.dataset.alertAck)}/acknowledge`, { method: "POST" });
    showNotice("提醒已确认");
    await refreshAll();
  } else if (target.dataset.report) {
    await loadReport(target.dataset.report);
  } else if (target.dataset.aiTask) {
    await loadAiTask(target.dataset.aiTask);
  } else if (target.dataset.qualityRun) {
    await loadQualityRun(target.dataset.qualityRun);
  }
}

function bindEvents() {
  document.querySelectorAll(".nav-button").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.viewTarget));
  });
  document.addEventListener("click", (event) => {
    handleDelegatedClick(event).catch((error) => showNotice(error.message, true));
  });
  $("refresh-button").addEventListener("click", () => {
    refreshAll().then(() => showNotice("工作台已刷新")).catch((error) => showNotice(error.message, true));
  });
  $("user-select").addEventListener("change", () => {
    state.userId = $("user-select").value || null;
    state.selectedAiTaskId = null;
    refreshAll().catch((error) => showNotice(error.message, true));
  });
  $("global-asset-search").addEventListener("submit", (event) => {
    event.preventDefault();
    const query = $("global-asset-query").value;
    $("asset-query").value = query;
    searchAssets(query, true).catch((error) => showNotice(error.message, true));
  });
  $("asset-search-form").addEventListener("submit", (event) => {
    event.preventDefault();
    searchAssets($("asset-query").value).catch((error) => showNotice(error.message, true));
  });
  $("asset-select").addEventListener("change", () => {
    if ($("asset-select").value) {
      selectAsset($("asset-select").value).catch((error) => showNotice(error.message, true));
    }
  });
  $("market-data-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const assetId = requireSelectedAsset();
      const result = await api(
        `/assets/${encodeURIComponent(assetId)}/daily-bars/query`,
        {
          method: "POST",
          body: JSON.stringify(marketRequestPayload())
        }
      );
      renderMarketData(result);
      showNotice(
        result.data.records.length
          ? `已读取 ${result.data.records.length} 条日线`
          : "查询完成，但当前范围没有本地日线"
      );
    } catch (error) {
      showNotice(error.message, true);
    }
  });
  $("compute-factors-button").addEventListener("click", async () => {
    try {
      const assetId = requireSelectedAsset();
      $("compute-factors-button").disabled = true;
      const result = await api(
        `/assets/${encodeURIComponent(assetId)}/factors/compute`,
        {
          method: "POST",
          body: JSON.stringify(marketRequestPayload())
        }
      );
      renderMarketData(result);
      await loadAssetResearch(assetId);
      showNotice(`已重算并保存 ${result.values.length} 个因子值`);
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      $("compute-factors-button").disabled = false;
    }
  });
  $("watchlist-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const assetId = requireSelectedAsset();
      const tags = $("watchlist-tags").value.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean);
      await api(`/watchlist/${encodeURIComponent(assetId)}`, {
        method: "PUT",
        body: JSON.stringify({ reason: $("watchlist-reason").value, tags })
      });
      showNotice("自选研究状态已保存");
      await refreshAll();
    } catch (error) {
      showNotice(error.message, true);
    }
  });
  $("remove-watchlist-button").addEventListener("click", async () => {
    try {
      await api(`/watchlist/${encodeURIComponent(requireSelectedAsset())}`, { method: "DELETE" });
      showNotice("已移出自选");
      await refreshAll();
    } catch (error) {
      showNotice(error.message, true);
    }
  });
  $("asset-holding-form").addEventListener("submit", (event) => {
    event.preventDefault();
    saveHolding("asset-holding").catch((error) => showNotice(error.message, true));
  });
  $("holding-form").addEventListener("submit", (event) => {
    event.preventDefault();
    saveHolding("holding").catch((error) => showNotice(error.message, true));
  });
  $("remove-asset-holding-button").addEventListener("click", async () => {
    try {
      await removeHolding(requireSelectedAsset());
    } catch (error) {
      showNotice(error.message, true);
    }
  });
  $("remove-holding-button").addEventListener("click", async () => {
    try {
      await removeHolding(requireSelectedAsset());
    } catch (error) {
      showNotice(error.message, true);
    }
  });
  $("valuation-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const prices = {};
      document.querySelectorAll("[data-price-asset]").forEach((input) => {
        prices[input.dataset.priceAsset] = Number(input.value);
      });
      const result = await api("/portfolio/valuations", {
        method: "POST",
        body: JSON.stringify({ as_of_date: $("valuation-date").value, prices })
      });
      const generated = result.generated_alerts.length;
      showNotice(`组合快照已保存${generated ? `，并生成 ${generated} 条提醒` : ""}`);
      await refreshAll();
      await loadSnapshot(result.valuation.snapshot.snapshot_id);
    } catch (error) {
      showNotice(error.message, true);
    }
  });
  $("alert-filter").addEventListener("change", renderAlerts);
  $("price-rule-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/alert-rules/price", {
        method: "POST",
        body: JSON.stringify({
          asset_id: requireSelectedAsset(),
          rule_type: $("price-rule-type").value,
          threshold: Number($("price-rule-threshold").value)
        })
      });
      showNotice("价格提醒规则已创建");
      await refreshAll();
    } catch (error) {
      showNotice(error.message, true);
    }
  });
  $("event-rule-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/alert-rules/event", {
        method: "POST",
        body: JSON.stringify({
          event_type: $("event-rule-type").value,
          asset_id: $("event-rule-global").checked ? null : requireSelectedAsset()
        })
      });
      showNotice("事件提醒规则已创建");
      await refreshAll();
    } catch (error) {
      showNotice(error.message, true);
    }
  });
  $("weight-rule-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/alert-rules/max-weight", {
        method: "POST",
        body: JSON.stringify({ threshold: Number($("weight-rule-threshold").value) / 100 })
      });
      showNotice("组合集中度规则已创建");
      await refreshAll();
    } catch (error) {
      showNotice(error.message, true);
    }
  });
  $("price-observation-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const alerts = await api("/alerts/evaluate-price", {
        method: "POST",
        body: JSON.stringify({
          asset_id: requireSelectedAsset(),
          price: Number($("observed-price").value),
          observed_at: $("observed-at").value
        })
      });
      showNotice(alerts.length ? `生成 ${alerts.length} 条新提醒` : "观察值未触发新提醒");
      await refreshAll();
    } catch (error) {
      showNotice(error.message, true);
    }
  });
  $("report-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const report = await api("/reports", {
        method: "POST",
        body: JSON.stringify({
          asset_id: requireSelectedAsset(),
          title: $("report-title").value,
          summary: $("report-summary").value,
          content_markdown: $("report-content").value,
          status: $("report-status").value
        })
      });
      event.target.reset();
      showNotice("分析报告已创建");
      await refreshAll();
      await loadReport(report.report_id);
    } catch (error) {
      showNotice(error.message, true);
    }
  });
  $("ai-task-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const task = await api("/ai/tasks", {
        method: "POST",
        body: JSON.stringify({
          asset_id: requireSelectedAsset(),
          question: $("ai-question").value
        })
      });
      showNotice("AI 研究上下文已冻结");
      await refreshAll();
      await loadAiTask(task.task_id);
    } catch (error) {
      showNotice(error.message, true);
    }
  });
  $("execute-ai-button").addEventListener("click", async () => {
    try {
      if (!state.selectedAiTaskId) throw new Error("请先选择 AI 任务");
      $("execute-ai-button").disabled = true;
      await api(`/ai/tasks/${encodeURIComponent(state.selectedAiTaskId)}/execute`, { method: "POST" });
      showNotice("AI 任务执行完成");
      await refreshAll();
      await loadAiTask(state.selectedAiTaskId);
    } catch (error) {
      $("execute-ai-button").disabled = false;
      showNotice(error.message, true);
    }
  });
  $("user-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const user = await api("/users", {
        method: "POST",
        body: JSON.stringify({ display_name: $("new-user-name").value })
      });
      await loadUsers(user.user_id);
      event.target.reset();
      await refreshAll();
      showNotice("用户空间已创建并切换");
    } catch (error) {
      showNotice(error.message, true);
    }
  });
}

async function initialize() {
  bindEvents();
  const today = new Date().toISOString().slice(0, 10);
  const marketStart = new Date();
  marketStart.setDate(marketStart.getDate() - 180);
  $("valuation-date").value = today;
  $("market-start-date").value = marketStart.toISOString().slice(0, 10);
  $("market-end-date").value = today;
  $("observed-at").value = new Date().toISOString();
  setView(location.hash.slice(1) || "dashboard");
  await loadUsers();
  await searchAssets("");
  await refreshAll();
}

initialize().catch((error) => showNotice(error.message, true));
