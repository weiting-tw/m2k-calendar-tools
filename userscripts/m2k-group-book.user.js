// ==UserScript==
// @name         m2k 群組排會議助手 (Mail2000 Group Booking)
// @namespace    gss.m2k.groupbook
// @version      0.4.1
// @description  Mail2000 會議排程：一站式填會議資訊 + 搜人 / 搜部門(自動展開成員) / 貼email，批次加入與會者並一鍵建立。同源、沿用登入、免 CORS、免 token。
// @match        https://mail.gss.com.tw/cgi-bin/cal/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==
/*
 安裝：Tampermonkey（新版 Chrome 需到 chrome://extensions → Tampermonkey → 詳細資料
       → 開「允許使用者指令碼」）→ 新增腳本、貼上、存檔 → 重整行事曆頁。
 用法：Mail2000「會議排程」頁 → 右下「👥 群組排會議」開面板：
   A. 填會議資訊（標題/日期/開始/結束/地點）
   B. 加入與會者：① 搜姓名 ② 搜部門（會自動展開全部成員） ③ 貼 email
   C. 按「✅ 建立會議」→ 自動填入原生表單、勾寄送通知信、儲存（存前會再確認一次）
 註：搜「部門」才會展開群組；直接打群組信箱(如 xxx@gss.com.tw)只是一個收件者、不會展開。
*/
(function () {
  "use strict";
  const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // 通訊錄回來的姓名/部門是伺服器 HTML 抽出的文字，塞回 innerHTML 前必須跳脫
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const scrapeEmails = (root) =>
    [...new Set(([...root.querySelectorAll("td,span,a,div")].map((e) => e.textContent).join(" ")
      .match(EMAIL_RE) || []).map((e) => e.toLowerCase()))];

  /* ---------- 加入與會者（原生輸入框 + Enter，已實測）---------- */
  const SEL = { input: ".scheduleAttendeeInput", list: ".scheduleAttendeeList", item: ".scheduleAttendee" };
  const existing = () => { const s = new Set(); document.querySelectorAll(`${SEL.list} ${SEL.item}[data-id]`).forEach((x) => s.add((x.getAttribute("data-id") || "").toLowerCase())); return s; };
  async function addOne(email) {
    const inp = document.querySelector(SEL.input);
    if (!inp) throw new Error("找不到與會者欄位，請在「會議排程」頁使用。");
    inp.focus(); inp.value = email; inp.dispatchEvent(new Event("input", { bubbles: true }));
    for (const t of ["keydown", "keypress", "keyup"]) inp.dispatchEvent(new KeyboardEvent(t, { bubbles: true, key: "Enter", keyCode: 13, which: 13 }));
    await sleep(380); inp.value = "";
  }
  async function addMany(emails, log) {
    const have = existing(); const todo = [];
    emails.forEach((e) => { const k = e.toLowerCase(); if (k && !have.has(k) && !todo.includes(e)) todo.push(e); });
    if (!todo.length) { log("沒有新成員可加入（可能都已在名單）。"); return 0; }
    log(`加入 ${todo.length} 位…`); let ok = 0;
    for (const e of todo) { try { const b = existing().size; await addOne(e); if (existing().size > b) ok++; } catch (err) { log("✗ " + e); } }
    log(`已加入 ${ok}/${todo.length} 位（目前與會者 ${existing().size} 人）。`);
    return ok;
  }
  function ensureNotify(log) {
    let cb = null;
    document.querySelectorAll("label,span,td,div").forEach((el) => { const t = el.textContent || ""; if (!cb && /寄送通知信/.test(t) && t.length < 12) cb = el.querySelector('input[type=checkbox]') || (el.parentElement && el.parentElement.querySelector('input[type=checkbox]')); });
    if (cb && !cb.checked) { cb.click(); log && log("已勾選「寄送通知信」。"); }
  }

  /* ---------- 搜「人」(mds) ---------- */
  let SCOPE = null;
  async function getScope() {
    if (SCOPE) return SCOPE;
    const t = await (await fetch("/cgi-bin/adb2tree?tofield=widget", { credentials: "include" })).text();
    const m = /do_switchto\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]*)['"]/.exec(t);
    if (!m) throw new Error("取不到通訊錄範圍。");
    return (SCOPE = { abid: m[1], dirid: m[2] });
  }
  function pickName(cells) {
    let wp = cells.find((c) => /[A-Za-z一-龥].*[（(].*[）)]/.test(c) && !c.includes("@"));
    if (wp) return wp.trim();
    let b = ""; cells.forEach((c) => { if (c && !c.includes("@") && !c.includes("/") && c.length <= 24 && c.length > b.length) b = c; });
    return b.trim();
  }
  async function searchPeople(q) {
    const sc = await getScope();
    const url = "/cgi-bin/adb2search_mds?command=mdssearch&tofield=widget&queryfield=&querystring=" +
      encodeURIComponent(q) + "&workingabid=" + encodeURIComponent(sc.abid) + "&workingdirid=" + encodeURIComponent(sc.dirid);
    const doc = new DOMParser().parseFromString(await (await fetch(url, { credentials: "include" })).text(), "text/html");
    const seen = new Set(), out = [];
    doc.querySelectorAll("tr").forEach((tr) => {
      const em = (tr.innerHTML.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/) || [])[0];
      if (em && !seen.has(em.toLowerCase())) { seen.add(em.toLowerCase()); out.push({ name: pickName([...tr.querySelectorAll("td")].map((td) => (td.textContent || "").trim())) || em, email: em }); }
    });
    return out;
  }

  /* ---------- 搜「部門」→ 自動展開成員 ---------- */
  let DEPTS = null, GSSABID = "";
  async function loadDepts() {
    if (DEPTS) return DEPTS;
    // 1) 先取 GSS 通訊錄 abid（tree_mds expand 需要 workingabid 才會回子部門）
    const pt = await (await fetch("/cgi-bin/adb2tree?tofield=widget", { credentials: "include" })).text();
    const sw = /do_switchto\(\s*['"]([^'"]+)['"]/.exec(pt); GSSABID = sw ? sw[1] : "";
    const seen = new Set(), depts = [];
    async function grab(dir) {
      const h = await (await fetch("/cgi-bin/adb2tree_mds?workingabid=" + encodeURIComponent(GSSABID) +
        "&command=expand&open_dirid=" + encodeURIComponent(dir) + "&tofield=widget", { credentials: "include" })).text();
      const doc = new DOMParser().parseFromString(h, "text/html");
      doc.querySelectorAll("a,[onclick]").forEach((el) => {
        const m = /do_opendir\(\s*['"]([^'"]+)['"]/.exec(el.getAttribute("onclick") || "");
        const name = (el.textContent || "").trim();
        if (m && name && !seen.has(m[1])) { seen.add(m[1]); depts.push({ name, path: m[1] }); }
      });
    }
    await grab("");           // 頂層 + 已展開者
    await grab("/GSS_EMP");    // 公司員工部門樹（BMD/CSBDBG/…）
    return (DEPTS = depts);
  }
  async function listDeptMembers(path, statusFn) {
    const emails = new Set();
    for (let p = 1; p <= 40; p++) {
      const u = "/cgi-bin/adb2main_mds?command=list&tofield=widget&workingabid=" + encodeURIComponent(GSSABID) +
        "&workingdirid=" + encodeURIComponent(path) + "&pageno=" + p;
      const doc = new DOMParser().parseFromString(await (await fetch(u, { credentials: "include" })).text(), "text/html");
      const before = emails.size;
      scrapeEmails(doc).forEach((e) => emails.add(e));
      if (emails.size === before) break;
      statusFn && statusFn(`讀取第 ${p} 頁…（${emails.size} 人）`);
    }
    return [...emails];
  }

  /* ---------- 填原生表單 + 儲存 ---------- */
  function setNative(id, val) { const el = document.getElementById(id); if (!el) return false; el.value = val; el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true })); return true; }
  function setDate(id, ymd) {
    const [y, m, d] = ymd.split("-").map(Number); const el = document.getElementById(id);
    try { if (window.jQuery && jQuery(el).datepicker) { jQuery(el).datepicker("setDate", new Date(y, m - 1, d)); return true; } } catch (e) {}
    return setNative(id, y + "/" + String(m).padStart(2, "0") + "/" + String(d).padStart(2, "0"));
  }

  /* ---------- UI ---------- */
  function buildPanel() {
    if (document.getElementById("m2kgb-btn")) return;
    const btn = document.createElement("button");
    btn.id = "m2kgb-btn"; btn.textContent = "👥 群組排會議";
    Object.assign(btn.style, { position: "fixed", right: "18px", bottom: "18px", zIndex: 999999, padding: "10px 14px", background: "#2563eb", color: "#fff", border: "none", borderRadius: "8px", cursor: "pointer", boxShadow: "0 2px 8px rgba(0,0,0,.25)", fontSize: "14px" });
    document.body.appendChild(btn);
    const panel = document.createElement("div");
    Object.assign(panel.style, { position: "fixed", right: "18px", bottom: "64px", zIndex: 999999, width: "370px", background: "#fff", border: "1px solid #cbd5e1", borderRadius: "10px", padding: "12px", boxShadow: "0 6px 24px rgba(0,0,0,.2)", font: "13px/1.5 sans-serif", display: "none", maxHeight: "84vh", overflow: "auto" });
    panel.innerHTML = `
      <div style="font-weight:600;margin-bottom:6px">群組排會議助手</div>
      <div style="color:#475569;margin:4px 0">A. 會議資訊</div>
      <input id="gb-title" placeholder="會議標題" style="width:100%;box-sizing:border-box;margin-bottom:4px">
      <div style="display:flex;gap:4px;margin-bottom:4px">
        <input id="gb-date" type="date" style="flex:1;min-width:0">
        <input id="gb-start" type="time" step="600" style="width:90px">
        <input id="gb-end" type="time" step="600" style="width:90px">
      </div>
      <input id="gb-loc" placeholder="地點（可空）" style="width:100%;box-sizing:border-box">
      <div id="gb-att" style="color:#16a34a;font-size:12px;margin:4px 0">目前與會者：0 人</div>

      <div style="color:#475569;margin:6px 0 2px">B. 加入與會者</div>
      <div style="display:flex;gap:4px">
        <select id="gb-mode" style="width:96px"><option value="person">搜姓名</option><option value="dept">搜部門</option></select>
        <input id="gb-q" placeholder="人名 / 部門(如 CSBDBG)" style="flex:1;min-width:0">
        <button id="gb-go" style="padding:4px 8px;background:#2563eb;color:#fff;border:none;border-radius:6px;cursor:pointer">搜尋</button>
      </div>
      <div id="gb-res" style="margin:5px 0;max-height:150px;overflow:auto;border:1px solid #e2e8f0;border-radius:6px;padding:4px;display:none"></div>
      <button id="gb-addsel" style="width:100%;padding:5px;background:#16a34a;color:#fff;border:none;border-radius:6px;cursor:pointer;display:none">加入勾選</button>
      <details style="margin-top:6px"><summary style="cursor:pointer;color:#64748b">貼上 email 批次加入</summary>
        <textarea id="gb-paste" rows="3" style="width:100%;box-sizing:border-box;margin-top:4px" placeholder="a@gss.com.tw&#10;b@gss.com.tw"></textarea>
        <button id="gb-addpaste" style="width:100%;padding:5px;background:#16a34a;color:#fff;border:none;border-radius:6px;cursor:pointer">加入貼上的</button>
      </details>

      <button id="gb-book" style="width:100%;padding:9px;margin-top:10px;background:#7c3aed;color:#fff;border:none;border-radius:8px;cursor:pointer;font-weight:600">✅ 建立會議</button>
      <div id="gb-log" style="margin-top:8px;max-height:110px;overflow:auto;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:6px;white-space:pre-wrap;color:#334155"></div>
    `;
    document.body.appendChild(panel);
    const $ = (s) => panel.querySelector(s);
    const logEl = $("#gb-log"); const log = (m) => { logEl.textContent += (logEl.textContent ? "\n" : "") + m; logEl.scrollTop = logEl.scrollHeight; };
    const refreshAtt = () => { $("#gb-att").textContent = `目前與會者：${existing().size} 人`; };
    setInterval(refreshAtt, 1500);
    btn.onclick = () => { panel.style.display = panel.style.display === "none" ? "block" : "none"; refreshAtt(); };

    const res = $("#gb-res"), addSel = $("#gb-addsel");
    let lastDeptMatches = [];
    // 人員：autocomplete（邊打邊跳、點一下即加入）
    let acTimer = null, acSeq = 0;
    async function doPersonLive(q) {
      const seq = ++acSeq;
      res.style.display = "block"; res.innerHTML = "搜尋中…"; addSel.style.display = "none";
      try {
        const list = await searchPeople(q);
        if (seq !== acSeq) return; // 已有更新的查詢，捨棄舊結果
        if (!list.length) { res.innerHTML = "查無此人。"; return; }
        res.innerHTML = ""; const have = existing();
        list.forEach((p) => {
          const added = have.has(p.email.toLowerCase());
          const row = document.createElement("div");
          row.style.cssText = "display:flex;justify-content:space-between;align-items:center;padding:3px 4px;cursor:pointer;border-radius:4px";
          row.innerHTML = `<span>${esc(p.name)} <span style="color:#94a3b8">&lt;${esc(p.email)}&gt;</span></span>`;
          const tag = document.createElement("span");
          tag.textContent = added ? "✓ 已加" : "＋ 加入";
          tag.style.cssText = "font-size:12px;white-space:nowrap;margin-left:6px;color:" + (added ? "#16a34a" : "#2563eb");
          row.appendChild(tag);
          row.onmouseenter = () => (row.style.background = "#f1f5f9");
          row.onmouseleave = () => (row.style.background = "");
          if (!added) row.onclick = async () => { row.onclick = null; await addMany([p.email], log); refreshAtt(); tag.textContent = "✓ 已加"; tag.style.color = "#16a34a"; };
          res.appendChild(row);
        });
      } catch (e) { res.innerHTML = "搜尋失敗：" + esc(e.message); }
    }
    async function doDeptSearch(q) {
      res.style.display = "block"; res.innerHTML = "搜尋中…"; addSel.style.display = "none";
      try {
        const depts = await loadDepts();
        const matches = depts.filter((d) => (d.name + d.path).toLowerCase().includes(q.toLowerCase()));
        if (!matches.length) { res.innerHTML = "查無此部門。可試 GSS_EMP 底下的代碼，如 CSBDBG。（純郵件群組無法展開）"; return; }
        res.innerHTML = "";
        matches.forEach((d) => {
          const row = document.createElement("div"); row.style.cssText = "display:flex;justify-content:space-between;align-items:center;padding:3px 0";
          row.innerHTML = `<span>${esc(d.name)} <span style="color:#94a3b8">${esc(d.path)}</span></span>`;
          const b = document.createElement("button"); b.textContent = "展開並加入"; b.style.cssText = "padding:3px 8px;background:#16a34a;color:#fff;border:none;border-radius:6px;cursor:pointer";
          b.onclick = async () => { b.disabled = true; b.textContent = "展開中…"; const em = await listDeptMembers(d.path, (s) => (b.textContent = s)); log(`部門 ${d.name}：${em.length} 位成員。`); await addMany(em, log); refreshAtt(); b.textContent = "已加入"; };
          row.appendChild(b); res.appendChild(row);
        });
      } catch (e) { res.innerHTML = "失敗：" + esc(e.message); }
    }
    function triggerSearch() {
      const q = $("#gb-q").value.trim(); if (!q) { res.style.display = "none"; return; }
      if ($("#gb-mode").value === "person") doPersonLive(q); else doDeptSearch(q);
    }
    $("#gb-go").onclick = triggerSearch;
    $("#gb-q").addEventListener("keydown", (e) => { if (e.key === "Enter") triggerSearch(); });
    $("#gb-q").addEventListener("input", () => {
      clearTimeout(acTimer);
      const q = $("#gb-q").value.trim();
      if ($("#gb-mode").value !== "person") return;      // 部門用按鈕搜
      const minLen = /[一-龥]/.test(q) ? 1 : 2;           // 中文1字即搜，英文2字
      if (q.length < minLen) { res.style.display = "none"; return; }
      acTimer = setTimeout(() => doPersonLive(q), 300);   // debounce
    });
    $("#gb-mode").addEventListener("change", () => { res.style.display = "none"; res.innerHTML = ""; $("#gb-q").placeholder = $("#gb-mode").value === "person" ? "打名字，會即時跳建議" : "部門代碼(如 CSBDBG) 後按搜尋"; });
    $("#gb-addpaste").onclick = async () => { const em = $("#gb-paste").value.match(EMAIL_RE) || []; if (!em.length) return log("沒抓到 email。"); await addMany(em, log); refreshAtt(); };

    $("#gb-book").onclick = async () => {
      const title = $("#gb-title").value.trim(), date = $("#gb-date").value, st = $("#gb-start").value, et = $("#gb-end").value, loc = $("#gb-loc").value.trim();
      if (!title || !date || !st || !et) return log("請填標題、日期、開始與結束時間。");
      setNative("scheduleEventSummary", title);
      setDate("scheduleEventStartDate", date); setDate("scheduleEventEndDate", date);
      setNative("scheduleEventStartTime", st); setNative("scheduleEventEndTime", et);
      if (loc) setNative("scheduleEventLocation", loc);
      ensureNotify(log);
      const n = existing().size;
      if (!confirm(`建立會議「${title}」\n${date} ${st}–${et}${loc ? "\n地點：" + loc : ""}\n與會者：${n} 人\n\n確定儲存並寄出通知？`)) return log("已取消。");
      const save = document.getElementById("publishSettingOK");
      if (save) { save.click(); log("✅ 已送出儲存。若跳出衝突/確認視窗，請依畫面確認。"); }
      else log("找不到儲存鈕，請手動按原生「儲存」。");
    };
  }

  const iv = setInterval(() => { if (document.body) { clearInterval(iv); buildPanel(); } }, 800);
})();
