"use strict";

const PAGE_SIZE = 50;

const PAGES = {
  overview: { title: "總覽", render: renderOverview },
  risk: { title: "風險參數", render: (c) => renderSettings(c, "risk") },
  channels: { title: "頻道授權與設定", render: renderChannels },
  "analysis-monitor": { title: "分析頻道監控參數" },
  runtime: { title: "運作參數", render: (c) => renderSettings(c, "runtime") },
  signals: { title: "訊號與核准記錄", render: renderSignals },
  orders: { title: "開單記錄", render: renderOrders },
  positions: { title: "持倉資料", render: renderPositions },
  pnl: { title: "損益記錄", render: renderPnl },
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
    execution_daily_loss_limit_reached: "每日虧損達熔斷門檻",
    trading_pause_decided: "每日虧損熔斷的選擇",
    manual_test_signal: "手動測試訊號",
    web_console_login_succeeded: "管理介面登入成功",
    web_console_login_failed: "管理介面登入失敗",
    web_console_logout: "管理介面登出",
  },
  closeReason: { STOP_LOSS: "停損", TAKE_PROFIT: "停利", OTHER: "其他（手動 / 強平）" },
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
    TRADING_PAUSED: "已暫停開單（每日虧損熔斷）",
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

function linkTile(labelText, value, tone, hash) {
  const node = tile(labelText, value, tone);
  node.classList.add("clickable");
  node.addEventListener("click", () => {
    window.location.hash = hash;
  });
  return node;
}

function pnlTone(value) {
  if (value === null || value === undefined) return "";
  const n = Number(value);
  return n > 0 ? "ok" : n < 0 ? "bad" : "";
}

function signed(value) {
  if (value === null || value === undefined) return "—";
  return Number(value) > 0 ? `+${value}` : String(value);
}

async function renderOverview(container) {
  const data = await api("/api/overview");
  if (data.database !== "available") {
    container.append(el("div", { class: "tiles" }, [tile("資料庫", "無法連線", "bad")]));
    return;
  }
  container.append(
    el("div", { class: "tiles" }, [
      linkTile("目前持倉", data.open_positions, "", "#positions"),
      linkTile("等待核准的訊號", data.awaiting_decision, data.awaiting_decision ? "warn-text" : "", "#signals"),
      tile("未處理的失敗通知", data.unhandled_failures, data.unhandled_failures ? "bad" : "ok"),
      tile(
        "開單狀態",
        data.paused_until ? `已停止，${fmtTime(data.paused_until)} 恢復` : "正常",
        data.paused_until ? "bad" : "ok",
      ),
      linkTile("今日淨損益 (USDT，台灣 08:00 起)", signed(data.today.net_pnl ?? "0"), pnlTone(data.today.net_pnl), "#pnl"),
      linkTile("累計淨損益 (USDT)", signed(data.overall.net_pnl ?? "0"), pnlTone(data.overall.net_pnl), "#pnl"),
    ]),
  );

  container.append(
    el("h2", { class: "section" }, "未處理的失敗通知"),
    el("p", { class: "note" }, "在 Telegram 按「我已處理」後就會從這裡消失。最多列出最近 20 筆。"),
    table([
      { title: "時間", render: (r) => fmtTime(r.recorded_at) },
      { title: "事件", render: (r) => label("eventType", r.event_type) },
      { title: "幣種", render: (r) => fmtNum(r.payload.symbol) },
      { title: "說明", render: (r) => fmtNum(r.payload.error || r.payload.reason || r.payload.orders || ""), wrap: true },
    ], data.unhandled_failure_events, (row) => {
      if (row.event_type.startsWith("execution_worker")) {
        openDrawer(label("eventType", row.event_type), () => [el("pre", { class: "json" }, JSON.stringify(row.payload, null, 2))]);
      } else {
        openDrawer("開單明細", () => orderDrawer(row.aggregate_id));
      }
    }, "沒有待處理的失敗通知"),
    el("h2", { class: "section" }, "最近平倉"),
    pnlTable(data.recent_closes, "還沒有平倉記錄"),
  );
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
  if (d.protection || d.take_profits.length) {
    nodes.push(el("h3", {}, "停損 / 停利單"), kv([
      ["停損價", d.protection ? d.protection.stop_price : "—"],
      ["停損數量", d.protection ? d.protection.protected_quantity : "—"],
      ["停損狀態", d.protection ? badge("orderState", d.protection.state) : "—"],
    ]));
    if (d.take_profits.length) {
      nodes.push(table([
        { title: "停利", render: (t) => `第 ${t.level_index + 1} 個` },
        { title: "價格", key: "target_price", num: true },
        { title: "數量", key: "target_quantity", num: true },
        { title: "狀態", render: (t) => badge("orderState", t.state) },
        { title: "確認時間", render: (t) => fmtTime(t.confirmed_at) },
      ], d.take_profits));
    }
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
    {
      title: "停利",
      render: (r) => (r.take_profits.length
        ? el("span", { class: "tp-levels" }, r.take_profits.map((t) => el("span", {}, [t.price, " ", badge("orderState", t.state)])))
        : "—"),
    },
    { title: "狀態", render: (r) => badge("state", r.state) },
  ], rows, (row) => openDrawer(`開單 ${row.symbol}`, () => orderDrawer(row.intent_id)), "目前沒有持倉"));
}

// ---------------------------------------------------------------- pnl

function pnlTable(rows, emptyText) {
  return table([
    { title: "平倉時間", render: (r) => fmtTime(r.closed_at) },
    { title: "頻道", key: "channel" },
    { title: "幣種", key: "symbol" },
    { title: "方向", render: (r) => sideCell(r.side) },
    { title: "數量", key: "quantity", num: true },
    { title: "進場價", key: "entry_price", num: true },
    { title: "出場價", key: "exit_price", num: true },
    { title: "平倉原因", render: (r) => label("closeReason", r.close_reason) },
    { title: "已實現損益", render: (r) => pnlCell(r.realized_pnl), num: true },
    { title: "平倉手續費", key: "exit_commission", num: true },
    { title: "淨損益", render: (r) => pnlCell(r.net_pnl), num: true },
  ], rows, (row) => openDrawer(`開單 ${row.symbol}`, () => orderDrawer(row.intent_id)), emptyText);
}

function rangeParams(range, from, to) {
  // Trading days start at UTC 00:00 (Taiwan 08:00), same as the daily-loss kill switch.
  const now = new Date();
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  if (range === "today") return { since: start.toISOString() };
  if (range === "7d" || range === "30d") {
    start.setUTCDate(start.getUTCDate() - (range === "7d" ? 6 : 29));
    return { since: start.toISOString() };
  }
  if (range === "custom") {
    const params = {};
    if (from) params.since = new Date(`${from}T00:00:00`).toISOString();
    if (to) {
      const end = new Date(`${to}T00:00:00`);
      end.setDate(end.getDate() + 1);
      params.until = end.toISOString();
    }
    return params;
  }
  return {};
}

const SVG_NS = "http://www.w3.org/2000/svg";

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

// Cumulative net PnL by day. One series, so no legend: the section heading names it.
function cumulativeChart(daily) {
  const wrap = el("div", { class: "card chart" });
  if (daily.length < 2) {
    wrap.append(el("div", { class: "empty" }, daily.length ? "只有一天的資料，累計曲線需要兩天以上" : "這段期間沒有平倉"));
    return wrap;
  }
  let running = 0;
  const points = daily.map((d) => {
    running += Number(d.net_pnl || 0);
    return { day: d.day, net: Number(d.net_pnl || 0), cum: running, trades: d.trades };
  });
  const width = 760;
  const height = 240;
  const pad = { top: 16, right: 16, bottom: 28, left: 64 };
  const values = points.map((p) => p.cum).concat(0);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const x = (i) => pad.left + (i / (points.length - 1)) * (width - pad.left - pad.right);
  const y = (v) => pad.top + (1 - (v - min) / (max - min)) * (height - pad.top - pad.bottom);

  const chart = svg("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart-svg", role: "img", "aria-label": "累計淨損益走勢" });
  for (const t of [min, (min + max) / 2, max]) {
    chart.append(svg("line", { x1: pad.left, x2: width - pad.right, y1: y(t), y2: y(t), class: "grid" }));
    const text = svg("text", { x: pad.left - 8, y: y(t) + 4, class: "axis", "text-anchor": "end" });
    text.textContent = t.toFixed(2);
    chart.append(text);
  }
  chart.append(svg("line", { x1: pad.left, x2: width - pad.right, y1: y(0), y2: y(0), class: "zero" }));
  for (const i of [0, points.length - 1]) {
    const text = svg("text", { x: x(i), y: height - 8, class: "axis", "text-anchor": i === 0 ? "start" : "end" });
    text.textContent = points[i].day;
    chart.append(text);
  }
  chart.append(svg("path", { d: points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.cum)}`).join(" "), class: "series" }));

  const cross = svg("line", { y1: pad.top, y2: height - pad.bottom, class: "crosshair", visibility: "hidden" });
  const dot = svg("circle", { r: 5, class: "series-dot", visibility: "hidden" });
  const hit = svg("rect", { x: pad.left, y: 0, width: width - pad.left - pad.right, height, fill: "transparent" });
  chart.append(cross, dot, hit);
  const tip = el("div", { class: "tooltip", hidden: true });
  hit.addEventListener("mousemove", (event) => {
    const box = chart.getBoundingClientRect();
    const sx = ((event.clientX - box.left) / box.width) * width;
    const ratio = (sx - pad.left) / (width - pad.left - pad.right);
    const i = Math.max(0, Math.min(points.length - 1, Math.round(ratio * (points.length - 1))));
    const p = points[i];
    for (const [attr, value] of [["x1", x(i)], ["x2", x(i)], ["visibility", "visible"]]) cross.setAttribute(attr, value);
    for (const [attr, value] of [["cx", x(i)], ["cy", y(p.cum)], ["visibility", "visible"]]) dot.setAttribute(attr, value);
    tip.replaceChildren(
      el("div", { class: "tip-title" }, p.day),
      el("div", {}, `當日淨損益 ${signed(p.net.toFixed(4))}（${p.trades} 筆）`),
      el("div", {}, `累計 ${signed(p.cum.toFixed(4))}`),
    );
    tip.hidden = false;
    const left = (x(i) / width) * box.width;
    tip.style.left = `${Math.min(left + 12, box.width - 190)}px`;
    tip.style.top = `${(y(p.cum) / height) * box.height}px`;
  });
  hit.addEventListener("mouseleave", () => {
    tip.hidden = true;
    cross.setAttribute("visibility", "hidden");
    dot.setAttribute("visibility", "hidden");
  });
  wrap.append(chart, tip);
  return wrap;
}

async function renderPnl(container) {
  const channels = await channelOptions();
  const summaryArea = el("div");
  const listArea = el("div");
  let offset = 0;
  const filters = filterBar([
    selectField("期間", "range", [["30d", "近 30 天"], ["7d", "近 7 天"], ["today", "今天"], ["all", "全部"], ["custom", "自訂"]], "30d"),
    inputField("起始日期（自訂）", "from", "", { type: "date" }),
    inputField("結束日期（自訂，含）", "to", "", { type: "date" }),
    selectField("頻道", "channel_id", channels, ""),
    selectField("平倉原因", "close_reason", optionsFrom("closeReason"), ""),
    inputField("幣種", "symbol", "", { placeholder: "例如 BTC" }),
  ], () => {
    offset = 0;
    refresh();
  });

  function params() {
    const f = filters.read();
    return { ...rangeParams(f.range, f.from, f.to), channel_id: f.channel_id, close_reason: f.close_reason, symbol: f.symbol };
  }

  async function refresh() {
    try {
      const [data, list] = await Promise.all([
        api(`/api/pnl/summary${query(params())}`),
        api(`/api/pnl${query({ ...params(), limit: PAGE_SIZE, offset })}`),
      ]);
      const s = data.summary;
      const winRate = s.win_rate === null ? "—" : `${(Number(s.win_rate) * 100).toFixed(1)}%`;
      const missing = s.trades - s.trades_with_pnl;
      summaryArea.replaceChildren(
        el("div", { class: "tiles" }, [
          tile("淨損益 (USDT)", signed(s.net_pnl ?? "0"), pnlTone(s.net_pnl)),
          tile("平倉筆數", s.trades),
          tile("勝率", winRate),
          tile("獲利 / 虧損筆數", `${s.wins} / ${s.losses}`),
          tile("最大單筆獲利", s.wins ? signed(s.best) : "—", s.wins ? pnlTone(s.best) : ""),
          tile("最大單筆虧損", s.losses ? signed(s.worst) : "—", s.losses ? pnlTone(s.worst) : ""),
        ]),
        el("p", { class: "note" }, `淨損益 = Binance 已實現損益 − 平倉手續費（本期共 ${fmtNum(s.fees ?? "0")} USDT），進場手續費目前未計入。${missing ? `有 ${missing} 筆平倉時取不到成交資料，未計入損益與勝率。` : ""}`),
        el("h2", { class: "section" }, "累計淨損益 (USDT)"),
        cumulativeChart(data.daily),
        el("h2", { class: "section" }, "每日統計（以 UTC 日期計，台灣 08:00 起算）"),
        table([
          { title: "日期", key: "day" },
          { title: "平倉筆數", key: "trades", num: true },
          { title: "獲利 / 虧損", render: (r) => `${r.wins} / ${r.losses}`, num: true },
          { title: "淨損益 (USDT)", render: (r) => pnlCell(r.net_pnl), num: true },
        ], [...data.daily].reverse(), null, "這段期間沒有平倉"),
        el("h2", { class: "section" }, "平倉明細"),
      );
      listArea.replaceChildren(
        pnlTable(list.items, "這段期間沒有平倉"),
        pager(list.total, offset, (next) => {
          offset = next;
          refresh();
        }),
      );
    } catch (error) {
      summaryArea.replaceChildren(el("div", { class: "card bad" }, error.message));
    }
  }

  container.append(filters.node, summaryArea, listArea);
  refresh();
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

// ---------------------------------------------------------------- settings

const SETTINGS_INTRO = {
  risk: "這裡的值會覆蓋 .env，執行服務下一輪（約 5 秒）就採用，不用重啟。只影響之後的新單，已開的倉位不會改變。",
  runtime: "各項目標示了生效方式；標示「需重啟」的要重啟對應服務才會採用。",
};

function withUnit(value, unit) {
  if (value === null || value === undefined) return "—";
  return unit === "%" ? `${value}%` : `${value} ${unit}`.trim();
}

async function renderSettings(container, group) {
  container.append(el("p", { class: "note" }, SETTINGS_INTRO[group]));
  const body = el("div");
  container.append(body);

  async function draw(data, message) {
    const inputs = new Map();
    const resets = new Set();
    const rows = data.items.map((item) => {
      const input = el("input", {
        type: "number",
        step: item.kind === "int" ? "1" : "any",
        min: item.minimum,
        max: item.maximum,
        value: item.effective ?? "",
        "aria-label": item.label,
      });
      inputs.set(item.key, { input, item });
      const source = item.override !== null
        ? el("span", { class: "badge info" }, `網頁設定（.env：${withUnit(item.env_value, item.unit)}）`)
        : el("span", { class: "badge" }, ".env 預設");
      const reset = item.override !== null
        ? el("button", {
          type: "button",
          class: "link-button",
          onclick: () => {
            resets.add(item.key);
            input.value = item.env_value ?? "";
            input.classList.add("changed");
          },
        }, "恢復 .env 預設")
        : null;
      input.addEventListener("input", () => {
        resets.delete(item.key);
        input.classList.toggle("changed", input.value !== (item.effective ?? ""));
      });
      return el("div", { class: "setting-row" }, [
        el("div", { class: "setting-text" }, [
          el("div", { class: "setting-label" }, item.label),
          el("div", { class: "muted small" }, item.help),
          el("div", { class: "muted small" }, `生效方式：${item.effect}`),
        ]),
        el("div", { class: "setting-input" }, [
          el("div", { class: "input-unit" }, [input, el("span", { class: "unit" }, item.unit)]),
          el("div", { class: "muted small" }, `範圍 ${withUnit(item.minimum, item.unit)} ～ ${withUnit(item.maximum, item.unit)}`),
          el("div", { class: "source" }, [source, reset]),
        ]),
      ]);
    });

    const note = el("input", { type: "text", placeholder: "修改原因（選填，會記在歷史裡）", maxlength: "500" });
    const status = el("div", { class: "save-status", role: "status" }, message || "");
    const save = el("button", { type: "button", class: "primary" }, "儲存變更");
    save.addEventListener("click", async () => {
      const changes = {};
      const lines = [];
      for (const [key, { input, item }] of inputs) {
        if (resets.has(key)) {
          changes[key] = null;
          lines.push(`${item.label}：${withUnit(item.effective, item.unit)} → .env 預設 ${withUnit(item.env_value, item.unit)}`);
        } else if (input.value !== (item.effective ?? "")) {
          changes[key] = input.value;
          lines.push(`${item.label}：${withUnit(item.effective, item.unit)} → ${withUnit(input.value, item.unit)}`);
        }
      }
      if (!lines.length) {
        status.textContent = "沒有任何變更";
        return;
      }
      if (!window.confirm(`確定要儲存以下變更？\n\n${lines.join("\n")}`)) return;
      save.disabled = true;
      try {
        const result = await api(`/api/settings/${group}`, {
          method: "POST",
          body: JSON.stringify({ changes, note: note.value }),
        });
        draw(result, `已儲存 ${result.changes.length} 項變更`);
      } catch (error) {
        status.textContent = error.message;
        status.classList.add("bad");
        save.disabled = false;
      }
    });

    const parts = [
      el("div", { class: "card settings-card" }, rows),
      el("div", { class: "save-bar" }, [note, save, status]),
    ];
    if (data.readonly.length) {
      parts.push(
        el("h2", { class: "section" }, "只能在 .env 修改的設定"),
        el("div", { class: "card" }, kv(data.readonly.map((r) => [r.label, r.value]))),
      );
    }
    parts.push(
      el("h2", { class: "section" }, "修改歷史"),
      table([
        { title: "時間", render: (r) => fmtTime(r.changed_at) },
        { title: "項目", key: "label" },
        { title: "從", render: (r) => (r.from === null ? ".env 預設" : withUnit(r.from, r.unit)) },
        { title: "改成", render: (r) => (r.to === null ? ".env 預設" : withUnit(r.to, r.unit)) },
        { title: "修改人", key: "changed_by" },
        { title: "原因", render: (r) => fmtNum(r.note), wrap: true },
      ], data.history, null, "還沒有從網頁修改過"),
    );
    body.replaceChildren(...parts);
  }

  draw(await api(`/api/settings/${group}`));
}

// ---------------------------------------------------------------- channels

const GATE_TONE = { MONITOR_ONLY: "good", ENABLED: "good", PAUSED: "warn", REJECTED: "bad" };
const AUTH_TONE = { GRANTED: "good", PENDING: "warn", UNKNOWN: "", REVOKED: "bad" };
const AUTH_FIELDS = [
  ["access_authorization", "讀取內容", "這個帳號是否可以讀取頻道內容"],
  ["automation_authorization", "自動收集", "是否允許自動收集訊息；不是「已授權」就不會收集"],
  ["ai_authorization", "AI 處理", "是否允許把訊息文字交給 AI 整理（分析草稿、分析情境）"],
  ["media_authorization", "圖片儲存", "是否允許儲存圖片，以及處理含未審核圖片的訊息"],
];
const CONTENT_LABELS = { TEXT: "文字", CAPTION: "圖片說明", IMAGE: "圖片" };

function choiceBadge(choices, tones, value) {
  return el("span", { class: `badge ${tones[value] || ""}` }, choices[value] || value);
}

async function renderChannels(container) {
  const data = await api("/api/channel-policies");
  const c = data.choices;
  const showRemoved = el("input", { type: "checkbox" });
  const list = el("div");
  container.append(
    el("p", { class: "note" }, "頻道設定存在資料庫，collector 每 5 分鐘重新讀取：新增或恢復的頻道會自動開始收集（不用重啟），暫停或移除的會停止收集。新增前請確認收集用的 Telegram 帳號已經加入該頻道。"),
    el("div", { class: "filters" }, [
      el("button", { type: "button", class: "primary", onclick: () => openDrawer("新增頻道", () => channelForm(c, null)) }, "＋ 新增頻道"),
      el("label", { class: "inline-check" }, [showRemoved, "顯示已移除的頻道"]),
    ]),
    list,
  );
  function draw() {
    const rows = data.items.filter((r) => showRemoved.checked || r.gate_decision !== "REJECTED");
    list.replaceChildren(table([
      { title: "名稱", key: "label", wrap: true },
      { title: "頻道 ID / 討論串", render: (r) => `${r.channel_id}${r.topic_id ? ` / ${r.topic_id}` : ""}` },
      { title: "類型", render: (r) => c.channel_types[r.channel_type] },
      { title: "狀態", render: (r) => choiceBadge(c.gate_decisions, GATE_TONE, r.gate_decision) },
      {
        title: "授權",
        // Short names, colored by status; the full status is in the hover title.
        render: (r) => el("span", { class: "auth-dots" }, AUTH_FIELDS.map(([f, name]) =>
          el("span", { class: `badge ${AUTH_TONE[r[f]] || ""}`, title: `${name}：${c.authorizations[r[f]]}` },
            `${name.slice(0, 2)}${r[f] === "GRANTED" ? "✓" : r[f] === "REVOKED" ? "✗" : "?"}`))),
      },
      { title: "幣種範圍", render: (r) => (r.symbol_scope_mode === "STATIC_ALLOWLIST" ? r.allowed_symbols.join("、") : "全部永續合約") },
      { title: "最後收到訊息", render: (r) => fmtTime(r.last_message_at) },
      { title: "已存訊息", key: "stored_messages", num: true },
    ], rows, (row) => openDrawer(row.label, () => channelForm(c, row)), "還沒有任何頻道"));
  }
  showRemoved.addEventListener("change", draw);
  draw();
}

function channelForm(c, row) {
  const isNew = row === null;
  const value = (field, fallback) => (row ? row[field] : fallback);
  const form = el("form", { class: "channel-form" });
  const field = (labelText, input, hint) =>
    el("label", { class: "form-field" }, [el("span", { class: "form-label" }, labelText), input, hint ? el("span", { class: "muted small" }, hint) : null]);
  const select = (name, choices, current) => {
    const node = el("select", { name });
    for (const [k, v] of Object.entries(choices)) node.append(el("option", { value: k, selected: k === current }, v));
    return node;
  };
  const text = (name, current, attrs = {}) => el("input", { name, value: current ?? "", ...attrs });

  if (isNew) {
    form.append(
      el("h3", {}, "頻道識別"),
      field("頻道 ID", text("channel_id", "", { required: true, placeholder: "例如 -1002439599598 或 2439599598" }), "可以直接貼 Telegram 顯示的 -100 開頭 ID"),
      field("討論串 ID", text("topic_id", "0", { inputmode: "numeric" }), "群組只收某個討論串時填它的 ID；整個頻道填 0"),
    );
  } else {
    form.append(kv([
      ["頻道 ID", String(row.channel_id)],
      ["討論串 ID", row.topic_id ? String(row.topic_id) : "整個頻道"],
      ["建立時間", fmtTime(row.created_at)],
      ["最後修改", fmtTime(row.updated_at)],
      ["最後收到訊息", fmtTime(row.last_message_at)],
    ]));
  }
  form.append(
    el("h3", {}, "基本設定"),
    field("名稱", text("label", value("label", ""), { required: true, maxlength: "200" })),
    field("公開帳號", text("username", value("username", ""), { placeholder: "例如 followgerry（不含 @）" }), "私人頻道留空"),
    field("頻道類型", select("channel_type", c.channel_types, value("channel_type", "ANALYSIS")), "下單訊號頻道會解析成訊號請你核准；分析頻道交給 AI 整理"),
    field("狀態", select("gate_decision", c.gate_decisions, value("gate_decision", "MONITOR_ONLY")), "「監看中」與「啟用」目前效果相同，都會收集；「暫停」與「已移除」不收集"),
    el("h3", {}, "授權"),
  );
  for (const [f, name, hint] of AUTH_FIELDS) {
    form.append(field(name, select(f, c.authorizations, value(f, "UNKNOWN")), hint));
  }
  const scope = select("symbol_scope_mode", c.symbol_scopes, value("symbol_scope_mode", "BINANCE_USDM_ACTIVE_PERPETUAL"));
  const contentBoxes = c.content_types.map((t) =>
    el("label", { class: "inline-check" }, [
      el("input", { type: "checkbox", name: "supported_content_types", value: t, checked: (value("supported_content_types", ["TEXT", "CAPTION"]) || []).includes(t) }),
      CONTENT_LABELS[t] || t,
    ]));
  form.append(
    el("h3", {}, "內容與幣種"),
    field("幣種範圍", scope),
    field("允許幣種", text("allowed_symbols", (value("allowed_symbols", []) || []).join(", ")), "「只限指定幣種」時必填，用逗號分隔，例如 BTCUSDT, ETHUSDT"),
    field("排除幣種", text("prohibited_symbols", (value("prohibited_symbols", []) || []).join(", ")), "這些幣種一律不處理"),
    field("訊息語言", text("message_languages", (value("message_languages", ["zh-TW"]) || []).join(", ")), "例如 zh-TW, en"),
    el("div", { class: "form-field" }, [el("span", { class: "form-label" }, "內容類型"), el("div", { class: "check-row" }, contentBoxes)]),
    field("原始訊息保留天數", text("raw_retention_days", value("raw_retention_days", 60), { type: "number", min: "1", max: "3650" }), "超過天數的原始訊息與衍生的訊號、核准會每天自動刪除（有開單的保留）"),
    el("h3", {}, "授權紀錄"),
    field("授權確認人", text("acceptance_owner", value("acceptance_owner", "User"))),
    field("授權確認日期", text("acceptance_date", value("acceptance_date", ""), { type: "date" })),
    field("說明文件路徑", text("onboarding_doc_path", value("onboarding_doc_path", ""), { placeholder: "docs/phase-0/channels/….md" })),
  );

  const status = el("div", { class: "save-status", role: "status" });
  const readForm = () => {
    const fd = new FormData(form);
    const body = Object.fromEntries([...fd.entries()].filter(([k]) => k !== "supported_content_types"));
    body.supported_content_types = fd.getAll("supported_content_types");
    return body;
  };
  async function submit(body, confirmText) {
    if (confirmText && !window.confirm(confirmText)) return;
    status.classList.remove("bad");
    status.textContent = "儲存中…";
    try {
      if (isNew) {
        await api("/api/channel-policies", { method: "POST", body: JSON.stringify(body) });
      } else {
        const result = await api(`/api/channel-policies/${row.channel_id}/${row.topic_id}`, { method: "PUT", body: JSON.stringify(body) });
        if (!result.changes.length) {
          status.textContent = "沒有任何變更";
          return;
        }
      }
      channelCache = null; // other pages' channel filters
      closeDrawer();
      route();
    } catch (error) {
      status.textContent = error.message;
      status.classList.add("bad");
    }
  }
  const buttons =[el("button", { type: "submit", class: "primary" }, isNew ? "新增頻道" : "儲存變更")];
  if (!isNew && row.gate_decision !== "REJECTED") {
    buttons.push(el("button", {
      type: "button",
      class: "danger",
      onclick: () => submit({ ...readForm(), gate_decision: "REJECTED" }, `確定要移除「${row.label}」？\n\n會停止收集這個頻道；已收集的資料照保留天數清除。之後可以把狀態改回「監看中」恢復。`),
    }, "移除頻道"));
  }
  form.append(el("div", { class: "save-bar" }, [...buttons, status]));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const body = readForm();
    const turningOn = body.automation_authorization === "GRANTED" && ["MONITOR_ONLY", "ENABLED"].includes(body.gate_decision);
    submit(body, isNew && turningOn ? `新增「${body.label}」後，collector 會在 5 分鐘內開始收集。\n請確認收集用的 Telegram 帳號已加入這個頻道。` : null);
  });
  return form;
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
