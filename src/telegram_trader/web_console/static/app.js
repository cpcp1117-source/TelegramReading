"use strict";

const PAGES = {
  overview: { title: "總覽", render: renderOverview },
  risk: { title: "風險參數" },
  channels: { title: "頻道授權與設定" },
  "analysis-monitor": { title: "分析頻道監控參數" },
  runtime: { title: "運作參數" },
  signals: { title: "訊號與核准記錄" },
  orders: { title: "開單記錄" },
  positions: { title: "持倉資料" },
  pnl: { title: "損益記錄" },
  theses: { title: "分析草稿" },
  audit: { title: "稽核日誌" },
};

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

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function tile(label, value, tone) {
  return el("div", { class: "card tile" }, [
    el("div", { class: "label" }, label),
    el("div", { class: `value ${tone || ""}` }, value),
  ]);
}

async function renderOverview(container) {
  const data = await api("/api/overview");
  const dbOk = data.database === "available";
  container.append(
    el("div", { class: "tiles" }, [
      tile("資料庫", dbOk ? "正常" : "無法連線", dbOk ? "ok" : "bad"),
    ]),
  );
}

function renderPending(container) {
  container.append(el("div", { class: "card muted" }, "此頁將於後續步驟完成。"));
}

async function route() {
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
