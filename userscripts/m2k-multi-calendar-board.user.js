// ==UserScript==
// @name         m2k 多人行事曆看板 (Mail2000 Multi-Calendar Board)
// @namespace    gss.m2k.multiboard
// @version      0.1.0
// @description  在 Mail2000 行事曆頁，合併檢視自己與他人/公用行事曆，做成看板（每天一欄）。同源、沿用登入、免 CORS、免 token。
// @match        https://mail.gss.com.tw/cgi-bin/cal/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==
/*
 安裝：Tampermonkey（新版 Chrome 需開「允許使用者指令碼」）→ 新增腳本貼上 → 重整行事曆頁。
 用法：行事曆頁右下「🗓 多人看板」→ 勾選要看的行事曆（你自己 / 他人 / 公用）→ 設天數 → 產生看板。
 資料來源：webmail feeds API（同源、用你現有登入），只會看到你本來就有權限的行事曆。
*/
(function () {
  "use strict";
  const BASE = "/cgi-bin/cal/calsrv/feeds/default/";
  const _esc = (s) => (s || "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const _URL = /(https?:\/\/[^\s<>"')\]]+)/g;
  const linkify = (t) => (t || "").split(_URL).map((p, i) =>
    i % 2 ? `<a href="${_esc(p)}" target="_blank" rel="noopener">${_esc(p)}</a>` : _esc(p).replace(/\n/g, "<br>")
  ).join("");
  const hexColor = (n) => "#" + ("000000" + ((n >>> 0) & 0xffffff).toString(16)).slice(-6);
  const WK = "一二三四五六日";

  async function getJSON(url) {
    const r = await fetch(url, { credentials: "include" });
    return r.json();
  }

  async function listCalendars() {
    const cals = [];
    for (const type of ["default", "subscribed", "public"]) {
      try {
        const j = await getJSON(BASE + type + "/");
        for (const c of (j.calendars || [])) {
          cals.push({
            feeds: c.feeds || type, id: String(c.id),
            name: c.display_name || (type + "/" + c.id),
            color: hexColor(c.color || 3238057), type,
          });
        }
      } catch (e) { /* 該類型可能沒有 */ }
    }
    return cals;
  }

  async function fetchEvents(cal, st, et) {
    const url = BASE + cal.feeds + "/" + cal.id + "/events/instances/?starttime=" + st + "&endtime=" + et;
    let j;
    try { j = await getJSON(url); } catch (e) { return []; }
    return (j.instances || []).map((e) => {
      let att = [];
      try { att = typeof e.attendee === "string" ? JSON.parse(e.attendee) : (e.attendee || []); } catch (_) {}
      return {
        start: new Date((e.dtstart + (e.offset || 0)) * 1000),
        end: e.dtend ? new Date((e.dtend + (e.offset || 0)) * 1000) : null,
        summary: e.summary || "(無標題)",
        loc: e.where || e.location || "",
        desc: (e.info && (e.info.description || e.info.content)) || e.description || "",
        organizer: (e.organizer || "").replace(/^mailto:/i, ""),
        attCount: Array.isArray(att) ? att.length : 0,
        calName: cal.name, color: cal.color,
      };
    });
  }

  function renderBoard(container, events, dayCount, startDate) {
    const byDay = new Map();
    for (let i = 0; i < dayCount; i++) {
      const d = new Date(startDate); d.setDate(d.getDate() + i);
      byDay.set(d.toDateString(), { date: new Date(d), items: [] });
    }
    events.sort((a, b) => a.start - b.start);
    for (const ev of events) {
      const k = ev.start.toDateString();
      if (byDay.has(k)) byDay.get(k).items.push(ev);
    }
    const hhmm = (d) => d.toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit", hour12: false });
    const cols = [];
    for (const { date, items } of byDay.values()) {
      const today = date.toDateString() === new Date().toDateString();
      const cards = items.map((ev) => {
        const t = ev.end ? `${hhmm(ev.start)}–${hhmm(ev.end)}` : hhmm(ev.start);
        const meta = [];
        if (ev.loc) meta.push(`<div class="mb-loc">📍 ${linkify(ev.loc)}</div>`);
        if (ev.organizer) meta.push(`<div class="mb-meta">👤 ${_esc(ev.organizer)}</div>`);
        if (ev.attCount) meta.push(`<div class="mb-meta">👥 ${ev.attCount} 人</div>`);
        const desc = (ev.desc && ev.desc.trim())
          ? `<details class="mb-desc"><summary></summary><div class="mb-descbody">${linkify(ev.desc)}</div></details>` : "";
        return `<div class="mb-card" style="border-left-color:${ev.color}">
          <div class="mb-cal"><span class="mb-dot" style="background:${ev.color}"></span>${_esc(ev.calName)}</div>
          <div class="mb-time">${t}</div><div class="mb-title">${linkify(ev.summary)}</div>
          ${meta.join("")}${desc}</div>`;
      }).join("") || '<div class="mb-empty">—</div>';
      cols.push(`<div class="mb-col${today ? " mb-today" : ""}">
        <div class="mb-hdr">${date.getMonth() + 1}/${date.getDate()} <span>週${WK[(date.getDay() + 6) % 7]}</span>${today ? " · 今天" : ""}</div>
        <div class="mb-cards">${cards}</div></div>`);
    }
    container.innerHTML = `<div class="mb-board">${cols.join("")}</div>`;
  }

  function ui() {
    if (document.getElementById("mb-btn")) return;
    const style = document.createElement("style");
    style.textContent = `
      #mb-overlay{position:fixed;inset:0;z-index:1000000;background:rgba(15,23,42,.5);display:none}
      #mb-modal{position:absolute;inset:24px;background:#f1f5f9;border-radius:12px;display:flex;flex-direction:column;overflow:hidden;font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif}
      #mb-top{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:12px 16px;background:#fff;border-bottom:1px solid #e2e8f0}
      #mb-top b{font-size:15px;margin-right:4px}
      #mb-cals{display:flex;gap:10px;flex-wrap:wrap;flex:1}
      #mb-cals label{font-size:13px;display:flex;align-items:center;gap:4px;cursor:pointer}
      #mb-body{flex:1;overflow:auto;padding:14px}
      .mb-board{display:flex;gap:12px;align-items:flex-start}
      .mb-col{flex:0 0 230px;background:#e2e8f0;border-radius:12px;overflow:hidden}
      .mb-col.mb-today{outline:2px solid #2563eb}
      .mb-hdr{background:#334155;color:#fff;font-weight:600;padding:8px 12px;font-size:14px}
      .mb-hdr span{opacity:.8;font-weight:400}
      .mb-cards{padding:8px;display:flex;flex-direction:column;gap:8px;min-height:40px}
      .mb-card{background:#fff;border-radius:8px;border-left:4px solid #2563eb;padding:8px 10px;box-shadow:0 1px 2px rgba(0,0,0,.08)}
      .mb-cal{font-size:10px;color:#64748b;display:flex;align-items:center;gap:4px;margin-bottom:2px}
      .mb-dot{width:8px;height:8px;border-radius:50%;display:inline-block}
      .mb-time{font-size:12px;color:#475569}
      .mb-title{font-size:13px;font-weight:600;margin-top:2px;line-height:1.35}
      .mb-loc,.mb-meta{font-size:11px;color:#64748b;margin-top:4px}
      .mb-card a{color:#2563eb;word-break:break-all}
      .mb-desc{margin-top:6px}
      .mb-desc>summary{font-size:11px;color:#2563eb;cursor:pointer;list-style:none}
      .mb-desc>summary::-webkit-details-marker{display:none}
      .mb-desc>summary::before{content:"▸ 描述"}
      .mb-desc[open]>summary::before{content:"▾ 描述"}
      .mb-descbody{font-size:11px;color:#334155;margin-top:6px;max-height:220px;overflow:auto;line-height:1.5;border-top:1px dashed #e2e8f0;padding-top:6px}
      .mb-empty{color:#94a3b8;text-align:center;padding:12px 0}
      #mb-btn{position:fixed;right:18px;bottom:66px;z-index:999999;padding:10px 14px;background:#0891b2;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:14px;box-shadow:0 2px 8px rgba(0,0,0,.25)}
      .mb-x{border:none;background:#e2e8f0;border-radius:6px;padding:6px 10px;cursor:pointer}
      .mb-go{border:none;background:#2563eb;color:#fff;border-radius:6px;padding:6px 12px;cursor:pointer}
      .mb-pub{flex-basis:100%}
      .mb-pub>summary{cursor:pointer;color:#0891b2;font-size:12px;list-style:revert}`;
    document.head.appendChild(style);

    const btn = document.createElement("button");
    btn.id = "mb-btn"; btn.textContent = "🗓 多人看板";
    document.body.appendChild(btn);

    const ov = document.createElement("div");
    ov.id = "mb-overlay";
    ov.innerHTML = `<div id="mb-modal">
      <div id="mb-top"><b>多人行事曆看板</b>
        <div id="mb-cals">載入行事曆清單…</div>
        <label>天數 <input id="mb-days" type="number" value="7" min="1" max="31" style="width:56px"></label>
        <button class="mb-go" id="mb-gen">產生</button>
        <button class="mb-x" id="mb-close">關閉</button>
      </div>
      <div id="mb-body"><p style="color:#64748b;padding:12px">勾選行事曆後按「產生」。</p></div>
    </div>`;
    document.body.appendChild(ov);

    const $ = (s) => ov.querySelector(s);
    let CALS = [];
    btn.onclick = async () => {
      ov.style.display = "block";
      if (!CALS.length) {
        CALS = await listCalendars();
        const chk = (i, c, on) => `<label><input type="checkbox" data-i="${i}" ${on ? "checked" : ""}>` +
          `<span class="mb-dot" style="background:${c.color}"></span>${_esc(c.name)}</label>`;
        const mine = [], subs = [], pubs = [];
        CALS.forEach((c, i) => (c.type === "default" ? mine : c.type === "subscribed" ? subs : pubs).push(chk(i, c, c.type !== "public")));
        $("#mb-cals").innerHTML =
          mine.join("") + subs.join("") +
          (pubs.length ? `<details class="mb-pub"><summary>公用 / 資源 (${pubs.length})</summary>
            <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:6px">${pubs.join("")}</div></details>` : "");
      }
    };
    $("#mb-close").onclick = () => (ov.style.display = "none");
    ov.onclick = (e) => { if (e.target === ov) ov.style.display = "none"; };
    $("#mb-gen").onclick = async () => {
      const days = Math.max(1, Math.min(31, parseInt($("#mb-days").value) || 7));
      const start = new Date(); start.setHours(0, 0, 0, 0);
      const end = new Date(start); end.setDate(end.getDate() + days);
      const st = Math.floor(start / 1000), et = Math.floor(end / 1000);
      const chosen = [...$("#mb-cals").querySelectorAll("input:checked")].map(c => CALS[+c.dataset.i]);
      if (!chosen.length) { $("#mb-body").innerHTML = '<p style="padding:12px">請至少勾一個行事曆。</p>'; return; }
      $("#mb-body").innerHTML = '<p style="padding:12px;color:#64748b">讀取中…</p>';
      const all = [];
      for (const cal of chosen) all.push(...await fetchEvents(cal, st, et));
      renderBoard($("#mb-body"), all, days, start);
    };
  }

  const iv = setInterval(() => { if (document.body) { clearInterval(iv); ui(); } }, 800);
})();
