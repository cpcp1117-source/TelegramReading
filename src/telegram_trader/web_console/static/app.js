"use strict";

const PAGE_SIZE = 50;

const PAGES = {
  overview: { title: "總覽", render: renderOverview },
  risk: { title: "風險參數" },
  channels: { title: "頻道授權與設定" },
  "analysis-monitor": { title: "分析頻道監控參數" },
  runtime: { title: "運作參數" },
  signals: { title: "訊號與核准記錄", render: renderSignals },
  orders: { title: "開單記錄", render: renderOrders },
  positions: { title: "持倉資料", render: renderPositions },
  pnl: { title: "損益記錄" },
  theses: { title: "分析草稿", render: renderTheses },
  audit: { title: "稽核日誌", render: renderAudit },
};

// ---------------------------------------------------------------- labels

const LABELS = {
  side: { LONG: "做多", SHORT: "做空", BUY: "買", SELL: "賣" },
  signalStatus: {
    NEW: "新訊號",
    INCOMPLETE: "資訊不完整",
    VALIDATED: "已驗證",
    CANCELLED: "已取消",
    EXPIRED: "已過期",
    SUPERSEDED: "已被取代",
  },
  outcome: {
    APPROVED: "核准",
    REJECTED: "拒絕",
    REJECTED_STALE: "拒絕（已非最新）",
    REJECTED_EXPIRED: "拒絕（已逾時）",
  },
  verdict: { APPROVED: "風控通過", REJECTED: "風控拒絕" },
  state: {
    PENDING_SUBMIT: "準備送單",
    SUBMITTED: "已送單",
    PARTIALLY_FILLED: "部分成交",
    FILLED: "已成交",
    PROTECTION_PENDING: "掛停損中",
    PROTECTED: "已掛停損",
    PROTECTION_FAILED: "停損掛單失敗",
    EMERGENCY_CLOSING: "緊急平倉中",
    CLOSING: "平倉中",
    CLOSED: "已平倉",
    FAILED_RECONCILIATION: "對帳失敗",
  },
  orderState: { PENDING: "等待確認", CONFIRMED: "已確認", FAILED: "失敗", EMERGENCY_CLOSED: "已緊急平倉" },
  purpose: { ENTRY: "進場", PROTECTION: "停損", TAKE_PROFIT: "停利", EMERGENCY_CLOSE: "緊急平倉" },
  entryType: { MARKET: "市價", LIMIT: "限價", RANGE: "區間" },
  stopOrigin: { AUTHOR: "訊號作者", DEFAULT_ROE_30: "系統預設", NONE: "無" },
  direction: { BULLISH: "看多", BEARISH: "看空", NEUTRAL: "中性" },
  thesisStatus: { DRAFT: "草稿", INSUFFICIENT_DATA: "資料不足" },
  confidence: { HIGH: "高", MEDIUM: "中", LOW: "低", INSUFFICIENT_DATA: "資料不足" },
  eventType: {
    "telegram.message.persisted": "收到頻道訊息",
    execution_skipped_expired: "風控判斷已逾時，未下單",
    execution_cancelled: "開單已取消",
    execution_entry_submitted: "進場單已送出",
    execution_entry_failed: "進場單失敗",
    execution_entry_not_filled: "進場單未成交",
    execution_entry_filled: "進場單已成交",
    execution_submitted: "已送單",
    execution_protection_retrying: "停損單重試中",
    execution_protection_confirmed: "停損單已確認",
    execution_protection_failed: "停損單失敗",
    execution_take_profit_retrying: "停利單重試中",
    execution_take_profit_confirmed: "停利單已確認",
    execution_take_profit_failed: "停利單失敗",
    execution_unexpected_error: "執行時發生非預期錯誤",
    execution_risk_rejected: "風控拒絕",
    execution_position_closed: "倉位已平倉",
    execution_leftover_cancel_failed: "殘留掛單取消失敗",
    execution_worker_error: "執行服務異常",
    execution_worker_recovered: "執行服務已恢復",
    execution_advisory: "執行提醒",
    manual_test_signal: "手動測試訊號",
    web_console_login_succeeded: "管理介面登入成功",
    web_console_login_failed: "管理介面登入失敗",
    web_console_logout: "管理介面登出",
  },
  reason: {
    STALE_MARKET_DATA: "行情資料過舊",
    SIGNAL_NOT_VALIDATED: "訊號未通過驗證",
    REFERENCE_PRICE_UNAVAILABLE: "取不到參考價格",
    PRICE_DEVIATION_TOO_HIGH: "現價偏離訊號價過大",
    RECEIVE_LAG_TOO_HIGH: "訊號收到太晚",
    SOURCE_TOO_OLD: "訊號來源過舊",
    DAILY_LOSS_KILL_SWITCH: "觸發每日虧損熔斷",
    SYMBOL_CONFLICT: "同幣種已有持倉",
    MAX_POSITIONS_REACHED: "已達最大持倉數",
    INVALID_STOP: "停損價不合理",
    QUANTITY_NOT_POSITIVE: "計算數量為零",
  },
};

const TONE = {
  APPROVED: "good",
  REJECTED: "bad",
  REJECTED_STALE: "bad",
  REJECTED_EXPIRED: "bad",
  VALIDATED: "good",
  NEW: "info",
  INCOMPLETE: "warn",
  CANCELLED: "bad",
  EXPIRED: "warn",
  PROTECTED: "good",
  CLOSED: "info",
  FILLED: "info",
  PROTECTION_FAILED: "bad",
  FAILED_RECONCILIATION: "bad",
  CONFIRMED: "good",
  FAILED: "bad",
  PENDING: "warn",
  BULLISH: "good",
  BEARISH: "bad",
};

function label(group, value) {
  if (value === null || value === undefined || value === "") return "—";
  return (LABELS[group] && LABELS[group][value]) || value;
}

function badge(group, value) {
  if (value === null || value === undefined) return el("span", { class: "muted" }, "—");
  return el("span", { class: `badge ${TONE[value] || ""}` }, label(group, value));
}

function sideCell(side) {
  if (!side) return "—";
  return el("span", { class: side === "LONG" || side === "BUY" ? "long" : "short" }, label("side", side));
}

// ---------------------------------------------------------------- helpers

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("未登入");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `請求失敗 (${response.status})`);
  }
  return response.json();
}

function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function fmtTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("zh-TW", { hour12: false });
}

function fmtNum(value) {
  return value === null || value === undefined || value === "" ? "—" : value;
}

function fmtList(values) {
  return values && values.length ? values.join(" / ") : "—";
}

function entryText(type, values) {
  return values && values.length ? `${label("entryType", type)} ${values.join(" / ")}` : label("entryType", type);
}

function pnlCell(value) {
  if (value === null || value === undefined) return el("span", { class: "muted" }, "—");
  const n = Number(value);
  return el("span", { class: n > 0 ? "long" : n < 0 ? "short" : "" }, n > 0 ? `+${value}` : value);
}

function tile(labelText, value, tone) {
  return el("div", { class: "card tile" }, [
    el("div", { class: "label" }, labelText),
    el("div", { class: `value ${tone || ""}` }, value),
  ]);
}

function kv(pairs) {
  const dl = el("dl", { class: "kv" });
  for (const [key, value] of pairs) {
    dl.append(el("dt", {}, key), el("dd", {}, value instanceof Node ? value : fmtNum(value)));
  }
  return dl;
}

function jsonBlock(value, summary = "原始資料") {
  return el("details", {}, [
    el("summary", {}, summary),
    el("pre", { class: "json" }, JSON.stringify(value, null, 2)),
  ]);
}

function selectField(text, name, options, value) {
  const select = el("select", { name });
  for (const [optValue, optText] of options) {
    select.append(el("option", { value: optValue, selected: optValue === value }, optText));
  }
  return el("label", {}, [text, select]);
}

function inputField(text, name, value, attrs = {}) {
  return el("label", {}, [text, el("input", { name, value: value || "", ...attrs })]);
}

function optionsFrom(group, includeAll = true) {
  const entries = Object.entries(LABELS[group]);
  return includeAll ? [["", "全部"], ...entries] : entries;
}

function filterBar(fields, onChange) {
  const form = el("form", { class: "filters" }, fields);
  const read = () => Object.fromEntries(new FormData(form).entries());
  form.addEventListener("change", () => onChange(read()));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    onChange(read());
  });
  return { node: form, read };
}

function table(columns, rows, onRowClick, emptyText = "沒有符合條件的記錄") {
  if (!rows.length) return el("div", { class: "table-wrap" }, el("div", { class: "empty" }, emptyText));
  const head = el("tr", {}, columns.map((c) => el("th", { class: c.num ? "num" : "" }, c.title)));
  const body = el(
    "tbody",
    {},
    rows.map((row) =>
      el(
        "tr",
        { class: onRowClick ? "clickable" : "", onclick: onRowClick ? () => onRowClick(row) : null },
        columns.map((c) => {
          const value = c.render ? c.render(row) : fmtNum(row[c.key]);
          return el("td", { class: [c.num ? "num" : "", c.wrap ? "wrap" : ""].join(" ") }, value);
        }),
      ),
    ),
  );
  return el("div", { class: "table-wrap" }, el("table", {}, [el("thead", {}, head), body]));
}

function pager(total, offset, onPage) {
  const from = total ? offset + 1 : 0;
  const to = Math.min(offset + PAGE_SIZE, total);
  return el("div", { class: "pager" }, [
    `第 ${from}–${to} 筆，共 ${total} 筆`,
    el("button", { type: "button", disabled: offset === 0, onclick: () => onPage(Math.max(0, offset - PAGE_SIZE)) }, "上一頁"),
    el("button", { type: "button", disabled: to >= total, onclick: () => onPage(offset + PAGE_SIZE) }, "下一頁"),
  ]);
}

// A filter bar + paged table that reloads in place when filters or page change.
function listView(container, { fields, load, columns, onRowClick }) {
  const results = el("div");
  let offset = 0;
  const filters = filterBar(fields, () => {
    offset = 0;
    refresh();
  });
  async function refresh() {
    try {
      const data = await load(filters.read(), offset);
      results.replaceChildren(
        table(columns, data.items, onRowClick),
        pager(data.total, offset, (next) => {
          offset = next;
          refresh();
        }),
      );
    } catch (error) {
      results.replaceChildren(el("div", { class: "card bad" }, error.message));
    }
  }
  container.append(filters.node, results);
  refresh();
}

function openDrawer(title, build) {
  closeDrawer();
  const body = el("div", {}, el("div", { class: "muted" }, "載入中…"));
  const drawer = el("aside", { class: "drawer", id: "drawer" }, [
    el("div", { class: "drawer-head" }, [
      el("h2", {}, title),
      el("button", { type: "button", onclick: closeDrawer }, "關閉"),
    ]),
    body,
  ]);
  document.body.append(el("div", { class: "drawer-backdrop", id: "drawer-backdrop", onclick: closeDrawer }), drawer);
  Promise.resolve(build())
    .then((nodes) => body.replaceChildren(...[].concat(nodes)))
    .catch((error) => body.replaceChildren(el("div", { class: "bad" }, error.message)));
}

function closeDrawer() {
  document.getElementById("drawer")?.remove();
  document.getElementById("drawer-backdrop")?.remove();
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDrawer();
});

let channelCache = null;
async function channelOptions() {
  if (!channelCache) channelCache = await api("/api/channels");
  const seen = new Set();
  const options = [["", "全部"]];
  for (const channel of channelCache) {
    if (seen.has(channel.channel_id)) continue;
    seen.add(channel.channel_id);
    options.push([String(channel.channel_id), channel.label]);
  }
  return options;
}

// ---------------------------------------------------------------- overview

async function renderOverview(container) {
  const data = await api("/api/overview");
  const dbOk = data.database === "available";
  container.append(el("div", { class: "tiles" }, [tile("資料庫", dbOk ? "正常" : "無法連線", dbOk ? "ok" : "bad")]));
}

// ---------------------------------------------------------------- signals

async function renderSignals(container) {
  const [facets, channels] = await Promise.all([api("/api/signals/facets"), channelOptions()]);
  const versions = facets.parser_versions;
  container.append(el("p", { class: "note" }, "預設只顯示最新解析版本；同一則訊息在不同解析版本各有一筆。核准仍在 Telegram 控制機器人操作。"));
  listView(container, {
    fields: [
      selectField("解析版本", "parser_version", [...versions.map((v, i) => [v, i === 0 ? `${v}（最新）` : v]), ["", "全部"]], versions[0] || ""),
      selectField("頻道", "channel_id", channels, ""),
      selectField("訊號狀態", "status", optionsFrom("signalStatus"), ""),
      selectField(
        "核准結果",
        "decision",
        [
          ["", "全部"],
          ["approved", "核准"],
          ["rejected", "拒絕"],
          ["pending", "已通知、未決定"],
          ["not_notified", "未通知"],
        ],
        "",
      ),
      inputField("幣種", "symbol", "", { placeholder: "例如 BTC" }),
    ],
    load: (f, offset) => api(`/api/signals${query({ ...f, limit: PAGE_SIZE, offset })}`),
    columns: [
      { title: "發布時間", render: (r) => fmtTime(r.source_date) },
      { title: "頻道", key: "channel" },
      { title: "幣種", key: "symbol" },
      { title: "方向", render: (r) => sideCell(r.side) },
      { title: "進場", render: (r) => entryText(r.entry_type, r.entry_values) },
      { title: "停損", key: "stop_value", num: true },
      { title: "停利", render: (r) => fmtList(r.take_profits) },
      { title: "訊號狀態", render: (r) => badge("signalStatus", r.status) },
      { title: "核准結果", render: (r) => (r.outcome ? badge("outcome", r.outcome) : el("span", { class: "muted" }, r.request_id ? "未決定" : "未通知")) },
      { title: "決定時間", render: (r) => fmtTime(r.decided_at) },
    ],
    onRowClick: (row) => openDrawer(`訊號 ${row.symbol || ""}`, () => signalDrawer(row.signal_row_id)),
  });
}

async function signalDrawer(id) {
  const d = await api(`/api/signals/${encodeURIComponent(id)}`);
  const nodes = [
    kv([
      ["頻道", d.channel],
      ["幣種 / 方向", el("span", {}, [d.symbol || "—", " ", sideCell(d.side)])],
      ["進場", entryText(d.entry_type, d.entry_values)],
      ["停損", `${fmtNum(d.stop_value)}（來源：${label("stopOrigin", d.stop_origin)}）`],
      ["停利", fmtList(d.take_profits)],
      ["訊號狀態", badge("signalStatus", d.status)],
      ["版本", `第 ${d.revision} 版 · 解析版本 ${d.parser_version}`],
      ["解析時間", fmtTime(d.created_at)],
      ["有效期限", fmtTime(d.expires_at)],
    ]),
  ];
  if (d.message) {
    nodes.push(el("h3", {}, "原始訊息"), el("div", { class: "quote" }, d.message.text || "（無文字）"),
      el("p", { class: "note" }, `發布 ${fmtTime(d.message.source_date)} · 收到 ${fmtTime(d.message.received_at)}`));
  }
  nodes.push(el("h3", {}, "核准流程"));
  if (!d.request) {
    nodes.push(el("p", { class: "muted" }, "這筆訊號沒有送到 Telegram 請你核准。"));
  } else {
    const items = [el("li", {}, [el("span", { class: "when" }, fmtTime(d.request.requested_at)), "送出核准請求"])];
    for (const edit of d.edits) {
      items.push(el("li", {}, [el("span", { class: "when" }, fmtTime(edit.edited_at)), `修改草稿：停損 ${fmtNum(edit.stop_value)}，停利 ${fmtList(edit.take_profits)}`]));
    }
    for (const decision of d.decisions) {
      const extra = decision.outcome === "APPROVED" ? `（停損 ${fmtNum(decision.approved_stop_value)}，停利 ${fmtList(decision.approved_take_profits)}）` : "";
      items.push(el("li", {}, [el("span", { class: "when" }, fmtTime(decision.decided_at)), badge("outcome", decision.outcome), ` ${extra}`]));
    }
    nodes.push(el("ul", { class: "timeline" }, items), el("p", { class: "note" }, `請求有效至 ${fmtTime(d.request.expires_at)}`));
  }
  if (d.intent_ids.length) {
    nodes.push(el("h3", {}, "對應開單"));
    for (const intentId of d.intent_ids) {
      nodes.push(el("button", { type: "button", onclick: () => openDrawer("開單明細", () => orderDrawer(intentId)) }, `查看開單 ${intentId.slice(0, 12)}…`));
    }
  }
  if (d.revisions.length > 1) {
    nodes.push(el("h3", {}, "同一訊號的所有版本"),
      table([
        { title: "版本", key: "revision" },
        { title: "狀態", render: (r) => badge("signalStatus", r.status) },
        { title: "時間", render: (r) => fmtTime(r.created_at) },
      ], d.revisions));
  }
  nodes.push(jsonBlock(d.evidence_spans, "解析依據"));
  return nodes;
}

// ---------------------------------------------------------------- orders

async function renderOrders(container) {
  listView(container, {
    fields: [
      selectField("風控結果", "verdict", optionsFrom("verdict"), ""),
      selectField("執行狀態", "state", [["", "全部"], ["NONE", "尚未執行"], ...Object.entries(LABELS.state)], ""),
      inputField("幣種", "symbol", "", { placeholder: "例如 BTC" }),
    ],
    load: (f, offset) => api(`/api/orders${query({ ...f, limit: PAGE_SIZE, offset })}`),
    columns: [
      { title: "建立時間", render: (r) => fmtTime(r.created_at) },
      { title: "頻道", key: "channel" },
      { title: "幣種", key: "symbol" },
      { title: "方向", render: (r) => sideCell(r.side) },
      { title: "數量", key: "quantity", num: true },
      { title: "成交均價", key: "avg_fill_price", num: true },
      { title: "停損", key: "stop_value", num: true },
      { title: "停利", render: (r) => fmtList(r.take_profits) },
      { title: "風控", render: (r) => badge("verdict", r.verdict) },
      { title: "執行狀態", render: (r) => (r.state ? badge("state", r.state) : el("span", { class: "muted" }, "尚未執行")) },
      { title: "狀態時間", render: (r) => fmtTime(r.state_at) },
    ],
    onRowClick: (row) => openDrawer(`開單 ${row.symbol}`, () => orderDrawer(row.intent_id)),
  });
}

async function orderDrawer(intentId) {
  const d = await api(`/api/orders/${encodeURIComponent(intentId)}`);
  const nodes = [
    kv([
      ["頻道", d.channel],
      ["幣種 / 方向", el("span", {}, [d.symbol, " ", sideCell(d.side)])],
      ["進場", entryText(d.entry_type, d.entry_values)],
      ["核准的停損", d.stop_value],
      ["核准的停利", fmtList(d.take_profits)],
      ["建立時間", fmtTime(d.created_at)],
      ["開單編號", d.intent_id],
    ]),
  ];
  const r = d.risk_decision;
  nodes.push(el("h3", {}, "風控判斷"));
  if (!r) {
    nodes.push(el("p", { class: "muted" }, "尚未經過風控。"));
  } else {
    nodes.push(kv([
      ["結果", badge("verdict", r.verdict)],
      ["拒絕原因", r.reason_codes.length ? r.reason_codes.map((c) => label("reason", c)).join("、") : "—"],
      ["下單數量", r.quantity],
      ["計算後停損價", r.computed_stop_price],
      ["採用進場價", r.entry_price_used],
      ["當時標記價", `${fmtNum(r.market_price)}（${fmtTime(r.market_price_fetched_at)}）`],
      ["採用資金", r.equity_used],
      ["當時持倉數", r.open_position_count_used],
      ["判斷有效至", fmtTime(r.expires_at)],
    ]));
  }
  nodes.push(el("h3", {}, "執行過程"));
  nodes.push(d.lifecycle.length
    ? el("ul", { class: "timeline" }, d.lifecycle.map((row) =>
      el("li", {}, [el("span", { class: "when" }, fmtTime(row.transitioned_at)), badge("state", row.state), row.transition_reason ? ` ${row.transition_reason}` : ""])))
    : el("p", { class: "muted" }, "尚未執行。"));
  if (d.protection || d.take_profit) {
    nodes.push(el("h3", {}, "停損 / 停利單"), kv([
      ["停損價", d.protection ? d.protection.stop_price : "—"],
      ["停損狀態", d.protection ? badge("orderState", d.protection.state) : "—"],
      ["停利價", d.take_profit ? d.take_profit.target_price : "—"],
      ["停利狀態", d.take_profit ? badge("orderState", d.take_profit.state) : "—"],
    ]));
  }
  if (d.exchange_orders.length) {
    nodes.push(el("h3", {}, "交易所訂單"), table([
      { title: "用途", render: (o) => label("purpose", o.purpose) },
      { title: "類型", key: "order_type" },
      { title: "方向", render: (o) => sideCell(o.side) },
      { title: "數量", key: "requested_quantity", num: true },
      { title: "觸發價", key: "requested_stop_price", num: true },
      { title: "成交量", key: "filled_quantity", num: true },
      { title: "均價", key: "avg_fill_price", num: true },
      { title: "狀態", key: "status" },
    ], d.exchange_orders));
    nodes.push(jsonBlock(d.exchange_orders.map((o) => ({ purpose: o.purpose, client_order_id: o.client_order_id, raw_response: o.raw_response })), "交易所原始回應"));
  }
  if (d.events.length) {
    nodes.push(el("h3", {}, "通知事件"), el("ul", { class: "timeline" }, d.events.map((e) =>
      el("li", {}, [el("span", { class: "when" }, fmtTime(e.recorded_at)), label("eventType", e.event_type)]))));
    nodes.push(jsonBlock(d.events.map((e) => ({ event_type: e.event_type, payload: e.payload })), "事件內容"));
  }
  return nodes;
}

// ---------------------------------------------------------------- positions

async function renderPositions(container) {
  const refresh = el("button", { type: "button", onclick: () => route() }, "重新整理");
  container.append(
    el("div", { class: "filters" }, [refresh]),
    el("p", { class: "note" }, "列出系統開的倉位（進場成交後、尚未偵測到平倉）。標記價取自 Binance Testnet 公開行情，未實現損益為估算值。"),
  );
  const rows = await api("/api/positions");
  container.append(table([
    { title: "開倉時間", render: (r) => fmtTime(r.opened_at) },
    { title: "頻道", key: "channel" },
    { title: "幣種", key: "symbol" },
    { title: "方向", render: (r) => sideCell(r.side) },
    { title: "數量", key: "quantity", num: true },
    { title: "進場均價", key: "entry_price", num: true },
    { title: "標記價", key: "mark_price", num: true },
    { title: "未實現損益 (USDT)", render: (r) => pnlCell(r.unrealized_pnl), num: true },
    { title: "停損", render: (r) => el("span", {}, [fmtNum(r.stop_price), " ", r.stop_state ? badge("orderState", r.stop_state) : ""]) },
    { title: "停利", render: (r) => el("span", {}, [fmtNum(r.take_profit_price), " ", r.take_profit_state ? badge("orderState", r.take_profit_state) : ""]) },
    { title: "狀態", render: (r) => badge("state", r.state) },
  ], rows, (row) => openDrawer(`開單 ${row.symbol}`, () => orderDrawer(row.intent_id)), "目前沒有持倉"));
}

// ---------------------------------------------------------------- theses

async function renderTheses(container) {
  const channels = await channelOptions();
  container.append(el("p", { class: "note" }, "分析頻道的 AI 解讀草稿，僅供參考，不會自動下單。"));
  listView(container, {
    fields: [
      selectField("頻道", "channel_id", channels, ""),
      selectField("方向", "direction", optionsFrom("direction"), ""),
      selectField("狀態", "status", optionsFrom("thesisStatus"), ""),
    ],
    load: (f, offset) => api(`/api/theses${query({ ...f, limit: PAGE_SIZE, offset })}`),
    columns: [
      { title: "時間", render: (r) => fmtTime(r.created_at) },
      { title: "頻道", key: "channel" },
      { title: "方向", render: (r) => badge("direction", r.primary_direction) },
      { title: "信心", render: (r) => label("confidence", r.confidence_status) },
      { title: "條件數", render: (r) => r.conditions.length, num: true },
      { title: "狀態", render: (r) => badge("thesisStatus", r.status) },
      { title: "原文摘要", key: "excerpt", wrap: true },
    ],
    onRowClick: (row) => openDrawer("分析草稿", () => thesisDrawer(row.thesis_row_id)),
  });
}

async function thesisDrawer(id) {
  const d = await api(`/api/theses/${encodeURIComponent(id)}`);
  const nodes = [
    kv([
      ["頻道", d.channel],
      ["方向", badge("direction", d.primary_direction)],
      ["信心", label("confidence", d.confidence_status)],
      ["狀態", badge("thesisStatus", d.status)],
      ["模型", `${d.llm_model}（schema ${d.extraction_schema_version}）`],
      ["時間", fmtTime(d.created_at)],
    ]),
    el("h3", {}, "原文"),
    el("div", { class: "quote" }, d.source_text_included),
  ];
  nodes.push(el("h3", {}, "條件"));
  nodes.push(d.conditions.length
    ? table([
      { title: "幣種", key: "symbol" },
      { title: "比較", key: "comparator" },
      { title: "觸發價", key: "trigger_price", num: true },
      { title: "意涵", render: (c) => badge("direction", c.implication_direction) },
    ], d.conditions)
    : el("p", { class: "muted" }, "沒有可監控的條件。"));
  if (d.evidence_quotes.length) {
    nodes.push(el("h3", {}, "引用依據"), ...d.evidence_quotes.map((q) => el("div", { class: "quote", style: "margin-bottom:6px" }, q)));
  }
  return nodes;
}

// ---------------------------------------------------------------- audit

async function renderAudit(container) {
  const facets = await api("/api/audit/facets");
  const toOptions = (values) => [["", "全部"], ...values.map((v) => [v, v])];
  listView(container, {
    fields: [
      selectField("事件類型", "event_type", [["", "全部"], ...facets.event_types.map((v) => [v, label("eventType", v)])], ""),
      selectField("對象類型", "aggregate_type", toOptions(facets.aggregate_types), ""),
      inputField("對象 ID", "aggregate_id", ""),
      inputField("起始日期", "since", "", { type: "date" }),
      inputField("結束日期（含）", "until", "", { type: "date" }),
    ],
    load: (f, offset) => {
      const params = { ...f, limit: PAGE_SIZE, offset };
      if (f.since) params.since = new Date(`${f.since}T00:00:00`).toISOString();
      if (f.until) {
        const end = new Date(`${f.until}T00:00:00`);
        end.setDate(end.getDate() + 1);
        params.until = end.toISOString();
      }
      return api(`/api/audit${query(params)}`);
    },
    columns: [
      { title: "時間", render: (r) => fmtTime(r.recorded_at) },
      { title: "事件類型", render: (r) => label("eventType", r.event_type) },
      { title: "對象類型", key: "aggregate_type" },
      { title: "對象 ID", render: (r) => (r.aggregate_id.length > 24 ? `${r.aggregate_id.slice(0, 24)}…` : r.aggregate_id) },
    ],
    onRowClick: (row) => openDrawer(label("eventType", row.event_type), () => [
      kv([
        ["時間", fmtTime(row.recorded_at)],
        ["事件類型", `${label("eventType", row.event_type)}（${row.event_type}）`],
        ["對象類型", row.aggregate_type],
        ["對象 ID", row.aggregate_id],
        ["事件 ID", row.event_id],
      ]),
      el("h3", {}, "內容"),
      el("pre", { class: "json" }, JSON.stringify(row.payload, null, 2)),
    ]),
  });
}

// ---------------------------------------------------------------- router

function renderPending(container) {
  container.append(el("div", { class: "card muted" }, "此頁將於後續步驟完成。"));
}

async function route() {
  closeDrawer();
  const key = (window.location.hash || "#overview").slice(1);
  const page = PAGES[key] ? key : "overview";
  for (const link of document.querySelectorAll(".sidebar a")) {
    link.classList.toggle("active", link.getAttribute("href") === `#${page}`);
  }
  const main = document.getElementById("page");
  main.replaceChildren(el("h1", {}, PAGES[page].title));
  document.title = `${PAGES[page].title}｜交易監控管理`;
  try {
    await (PAGES[page].render || renderPending)(main);
  } catch (error) {
    main.append(el("div", { class: "card bad" }, error.message));
  }
}

async function init() {
  const me = await api("/api/me");
  document.getElementById("username").textContent = me.username;
  document.getElementById("logout").addEventListener("click", async () => {
    await api("/api/logout", { method: "POST" });
    window.location.href = "/login";
  });
  window.addEventListener("hashchange", route);
  await route();
}

init();
