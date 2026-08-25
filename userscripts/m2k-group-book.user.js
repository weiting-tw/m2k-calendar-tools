// ==UserScript==
// @name         m2k 群組排會議助手 (Mail2000 Group Booking)
// @namespace    gss.m2k.groupbook
// @version      0.6.0
// @description  Mail2000 會議排程：一站式填會議資訊 + 搜人 / 搜部門(遞迴展開子部門與成員) / 貼email，批次加入與會者並一鍵建立。同源、沿用登入、免 CORS、免 token。
// @match        https://mail.gss.com.tw/cgi-bin/cal/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==
/*
 安裝：Tampermonkey（新版 Chrome 需到 chrome://extensions → Tampermonkey → 詳細資料
       → 開「允許使用者指令碼」）→ 新增腳本、貼上、存檔 → 重整行事曆頁。
 用法：Mail2000「會議排程」頁 → 右下「👥 群組排會議」開面板：
   A. 填會議資訊（標題/日期/開始/結束/地點）
   B. 加入與會者：① 搜姓名 ② 搜部門（遞迴展開該部門與所有子部門成員） ③ 貼 email
   C. 按「✅ 建立會議」→ 自動填入原生表單、勾寄送通知信、儲存（存前會再確認一次）
 註：搜「部門」才會展開群組；直接打群組信箱(如 xxx@example.com)只是一個收件者、不會展開。
*/
(function () {
  "use strict";
  const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // 通訊錄回來的姓名/部門是伺服器 HTML 抽出的文字，塞回 innerHTML 前必須跳脫
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ---------- 加入與會者（原生輸入框 + Enter，已實測）---------- */
  const SEL = { input: ".scheduleAttendeeInput", list: ".scheduleAttendeeList", item: ".scheduleAttendee" };
  const existing = () => { const s = new Set(); document.querySelectorAll(`${SEL.list} ${SEL.item}[data-id]`).forEach((x) => s.add((x.getAttribute("data-id") || "").toLowerCase())); return s; };
  // 按 Enter 後原生 widget 是非同步插 chip（疑似先發 XHR 驗信箱）。原本用固定 sleep(380)
  // 猜它做完：太短會把「稍後才出現」誤判成失敗而低報，太長則每人白等。改成等 DOM 真的變。
  const ADD_TIMEOUT_MS = 2000, ADD_POLL_MS = 50, MAX_FAIL_STREAK = 5;
  // 欄位藏著（會議排程對話框沒開）時 widget 不會收輸入，chip 永遠不出現。
  // querySelector 找得到隱藏的元素，所以不檢查可見性的話會每人白等滿一輪 timeout。
  const attendeeInput = () => {
    const inp = document.querySelector(SEL.input);
    if (!inp) throw new Error("找不到與會者欄位，請在「會議排程」頁使用。");
    if (!inp.offsetParent) throw new Error("與會者欄位藏著：請先開啟事件編輯畫面並切到「與會者」頁籤。");
    return inp;
  };
  async function addOne(email) {
    const inp = attendeeInput();
    const key = email.toLowerCase();
    inp.focus(); inp.value = email; inp.dispatchEvent(new Event("input", { bubbles: true }));
    for (const t of ["keydown", "keypress", "keyup"]) inp.dispatchEvent(new KeyboardEvent(t, { bubbles: true, key: "Enter", keyCode: 13, which: 13 }));
    for (let waited = 0; waited < ADD_TIMEOUT_MS; waited += ADD_POLL_MS) {
      await sleep(ADD_POLL_MS);
      // 認這個 email 自己的 chip，不是比總數：widget 重複插、或你同時手動刪人，數量都會騙人
      if (existing().has(key)) { inp.value = ""; return true; }
    }
    inp.value = "";
    return false;   // 等滿 2 秒它都沒出現，這時的「失敗」才是可信的
  }
  async function addMany(emails, log) {
    // 先確認欄位真的可用，否則每個人都會白等一輪 timeout 才失敗
    try { attendeeInput(); } catch (err) { log("✗ " + err.message); return 0; }
    const have = existing(), picked = new Set(), todo = [];
    emails.forEach((e) => {
      const k = (e || "").toLowerCase();
      if (k && !have.has(k) && !picked.has(k)) { picked.add(k); todo.push(e); }
    });
    if (!todo.length) { log("沒有新成員可加入（可能都已在名單）。"); return 0; }
    log(`加入 ${todo.length} 位…`);
    let ok = 0, streak = 0; const failed = [];
    for (let i = 0; i < todo.length; i++) {
      const e = todo[i];
      let done = false;
      try { done = await addOne(e); } catch (err) { done = false; }
      if (done) { ok++; streak = 0; } else { failed.push(e); streak++; }
      // 每次失敗要等滿 timeout，整批全掛會拖很久 → 連續失敗就認定壞了，停手
      if (streak >= MAX_FAIL_STREAK) {
        failed.push(...todo.slice(i + 1));
        log(`連續 ${streak} 位加不進去，中止（跳過剩下 ${todo.length - i - 1} 位）。`);
        break;
      }
      if (todo.length > 20 && (i + 1) % 20 === 0) log(`  …${i + 1}/${todo.length}`);
    }
    log(`已加入 ${ok}/${todo.length} 位（目前與會者 ${existing().size} 人）。`);
    if (failed.length) {   // 列出來才補得回去，別只報個數字
      log(`✗ ${failed.length} 位沒進去：${failed.slice(0, 10).join(", ")}` +
          (failed.length > 10 ? ` …等 ${failed.length} 位` : ""));
    }
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

  /* ---------- 搜「部門」→ 遞迴展開子部門 + 成員 ---------- */
  // 部門不是 adb2tree_mds 的樹節點 —— 那支不管換什麼參數都只回幾個頂層目錄，
  // do_opendir 裡永遠沒有部門（實機驗證過 10 種參數組合）。頂層目錄拿來當遞迴起點。
  // 真正的來源是 adb2main_mds 列表裡的 input[name=Entries]：
  //   adbetype="D" → 子部門（value 是完整路徑、nick 是名稱）
  //   adbetype="C" → 人（email 屬性是信箱、nick 是「英文名 (中文名)」）
  // 一支請求同時給「這層的人」和「這層的子部門」，所以遞迴不必另外建樹。
  let GSSABID = "", ROOTS = null, TOPDEPTS = null, ALLDEPTS = null;
  const PAGE_SIZE = 25;     // adb2main_mds 每頁固定 25 筆，未滿即最後一頁
  const MAX_PAGES = 40;     // 單一部門的分頁上限；成員多的部門會用到大半
  const MAX_NODES = 300;    // 遞迴節點上限
  // 截斷警告不能走 statusFn（那些字會被搜尋結果覆蓋），存起來倒進 log 面板
  const NOTES = [];
  const flushNotes = (log) => { while (NOTES.length) log("⚠ " + NOTES.shift()); };

  async function ensureAbid() {
    if (GSSABID) return GSSABID;
    const pt = await (await fetch("/cgi-bin/adb2tree?tofield=widget", { credentials: "include" })).text();
    // 取第一個「非空」abid：空的那個是個人通訊錄，第一個非空的才是公司通訊錄
    const sw = /do_switchto\(\s*['"]([^'"]+)['"]/.exec(pt);
    return (GSSABID = sw ? sw[1] : "");
  }

  // 樹端點雖然給不出部門，但它會列出頂層目錄 —— 拿來當遞迴起點，就不必寫死路徑
  async function rootDirs() {
    if (ROOTS) return ROOTS;
    await ensureAbid();
    const h = await (await fetch("/cgi-bin/adb2tree_mds?workingabid=" + encodeURIComponent(GSSABID) +
      "&command=expand&open_dirid=&tofield=widget", { credentials: "include" })).text();
    const out = new Set();
    for (const m of h.matchAll(/do_opendir\(\s*['"]([^'"]+)['"]/g)) {
      if (m[1] && m[1] !== "/") out.add(m[1]);   // "/" 是通訊錄自己，不是部門樹
    }
    if (!out.size) NOTES.push("樹端點沒回任何頂層目錄，搜部門會查不到東西。");
    return (ROOTS = [...out]);
  }

  async function fetchRows(path, page) {
    const u = "/cgi-bin/adb2main_mds?command=list&tofield=widget&workingabid=" + encodeURIComponent(GSSABID) +
      "&workingdirid=" + encodeURIComponent(path) + "&pageno=" + page;
    const doc = new DOMParser().parseFromString(await (await fetch(u, { credentials: "include" })).text(), "text/html");
    return [...doc.querySelectorAll('input[name="Entries"]')].map((el) => ({
      value: el.getAttribute("value") || "",
      nick: (el.getAttribute("nick") || "").trim(),
      email: (el.getAttribute("email") || "").trim().toLowerCase(),
      type: el.getAttribute("adbetype") || "",
    }));
  }

  // 一個部門的全部內容：人 + 直接子部門
  async function fetchNode(path, statusFn) {
    const members = new Map(), subs = new Map();
    let bottomed = false;
    for (let p = 1; p <= MAX_PAGES; p++) {
      const rows = await fetchRows(path, p);
      rows.forEach((r) => {
        if (r.type === "D") { if (r.value) subs.set(r.value, r.nick || r.value); }
        else if (r.email) members.set(r.email, r.nick);
      });
      statusFn && statusFn(`第 ${p} 頁…（${members.size} 人）`);
      if (rows.length < PAGE_SIZE) { bottomed = true; break; }   // 未滿頁 = 最後一頁
    }
    if (!bottomed) NOTES.push(`${path} 讀滿 ${MAX_PAGES} 頁仍未見底，成員可能不只 ${members.size} 人。`);
    return { members, subs };
  }

  // 只要子部門時的快版：目錄項排在成員項前面，所以看到第一個非目錄就知道目錄列完了
  async function fetchSubs(path) {
    const subs = new Map();
    for (let p = 1; p <= MAX_PAGES; p++) {
      const rows = await fetchRows(path, p);
      let sawNonDir = false;
      rows.forEach((r) => {
        if (r.type === "D") { if (r.value) subs.set(r.value, r.nick || r.value); }
        else sawNonDir = true;
      });
      if (sawNonDir || rows.length < PAGE_SIZE) break;
    }
    return subs;
  }

  // 搜部門用的清單：先只列各頂層目錄底下那一層（每個根一支請求），搜不到再往下遞迴
  async function loadDepts(statusFn) {
    if (TOPDEPTS) return TOPDEPTS;
    await ensureAbid();
    const roots = await rootDirs();
    statusFn && statusFn("讀取部門清單…");
    const all = new Map();
    for (const r of roots) (await fetchSubs(r)).forEach((n, p) => all.set(p, n));
    return (TOPDEPTS = [...all].map(([path, name]) => ({ path, name })));
  }

  // 整棵部門樹（頂層沒搜到時的後路）。部門數多時要跑上幾十秒，
  // 所以只在必要時跑、跑完快取，並且一路回報進度免得看起來像卡住。
  async function loadDeptsAll(statusFn) {
    if (ALLDEPTS) return ALLDEPTS;
    const all = new Map((await loadDepts(statusFn)).map((d) => [d.path, d.name]));
    const queue = [...all.keys()];
    let done = 0;
    while (queue.length && all.size < MAX_NODES) {
      const dir = queue.shift(); done++;
      statusFn && statusFn(`搜遍部門樹…（已找到 ${all.size} 個，查過 ${done}，待查 ${queue.length}）`);
      (await fetchSubs(dir)).forEach((n, p) => {
        if (!all.has(p)) { all.set(p, n); queue.push(p); }
      });
    }
    if (queue.length) NOTES.push(`部門數已達上限 ${MAX_NODES}，還有 ${queue.length} 個沒查，搜尋結果可能不全。`);
    return (ALLDEPTS = [...all].map(([path, name]) => ({ path, name })));
  }

  // 遞迴：本部門 + 所有子孫，邊走邊收，跨部門去重（同一人掛多部門只算一次）
  async function collectSubtree(root, statusFn) {
    const members = new Map(), visited = new Set(), queue = [root];
    while (queue.length && visited.size < MAX_NODES) {
      const dir = queue.shift();
      if (visited.has(dir)) continue;
      visited.add(dir);
      const short = dir.split("/").pop();
      const { members: ms, subs } = await fetchNode(dir, (s) =>
        statusFn && statusFn(`${short}（第 ${visited.size} 個部門，待展 ${queue.length}）${s}`));
      ms.forEach((n, e) => { if (!members.has(e)) members.set(e, n); });
      subs.forEach((_, d) => { if (!visited.has(d)) queue.push(d); });
    }
    if (queue.length) NOTES.push(`已達節點上限 ${MAX_NODES}，還有 ${queue.length} 個子部門沒展開。`);
    return { emails: [...members.keys()], nodeCount: visited.size };
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
        <input id="gb-q" placeholder="人名 / 部門代碼" style="flex:1;min-width:0">
        <button id="gb-go" style="padding:4px 8px;background:#2563eb;color:#fff;border:none;border-radius:6px;cursor:pointer">搜尋</button>
      </div>
      <div id="gb-res" style="margin:5px 0;max-height:150px;overflow:auto;border:1px solid #e2e8f0;border-radius:6px;padding:4px;display:none"></div>
      <button id="gb-addsel" style="width:100%;padding:5px;background:#16a34a;color:#fff;border:none;border-radius:6px;cursor:pointer;display:none">加入勾選</button>
      <details style="margin-top:6px"><summary style="cursor:pointer;color:#64748b">貼上 email 批次加入</summary>
        <textarea id="gb-paste" rows="3" style="width:100%;box-sizing:border-box;margin-top:4px" placeholder="a@example.com&#10;b@example.com"></textarea>
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
      res.style.display = "block"; res.innerHTML = "展開部門樹…"; addSel.style.display = "none";
      try {
        const ql = q.toLowerCase();
        const hit = (list) => list.filter((d) => (d.name + d.path).toLowerCase().includes(ql));
        let matches = hit(await loadDepts((s) => (res.textContent = s)));
        // 頂層沒中才搜整棵樹：深層的子部門代碼只有全樹找得到
        if (!matches.length) {
          res.textContent = "頂層沒有，改搜整棵部門樹（第一次會花點時間）…";
          matches = hit(await loadDeptsAll((s) => (res.textContent = s)));
        }
        flushNotes(log);
        if (!matches.length) { res.innerHTML = "查無此部門。請用通訊錄裡的部門代碼。（純郵件群組無法展開）"; return; }
        res.innerHTML = "";
        matches.forEach((d) => {
          const row = document.createElement("div"); row.style.cssText = "display:flex;justify-content:space-between;align-items:center;gap:4px;padding:3px 0";
          row.innerHTML = `<span>${esc(d.name)} <span style="color:#94a3b8">${esc(d.path)}</span></span>`;
          const bar = document.createElement("span"); bar.style.cssText = "display:flex;gap:4px;white-space:nowrap";
          // 主動作＝含子部門（要「整個部門的人」時的預設；子部門數要展開後才知道）
          const bAll = document.createElement("button");
          bAll.textContent = "展開全部並加入";
          bAll.style.cssText = "padding:3px 8px;background:#16a34a;color:#fff;border:none;border-radius:6px;cursor:pointer";
          bAll.onclick = async () => {
            bAll.disabled = true;
            const { emails, nodeCount } = await collectSubtree(d.path, (s) => (bAll.textContent = s));
            log(`部門 ${d.name}：${nodeCount} 個部門、${emails.length} 位成員。`);
            flushNotes(log);
            await addMany(emails, log); refreshAtt(); bAll.textContent = "已加入";
          };
          const bOne = document.createElement("button");
          bOne.textContent = "僅本層";
          bOne.style.cssText = "padding:3px 8px;background:#fff;color:#334155;border:1px solid #cbd5e1;border-radius:6px;cursor:pointer";
          bOne.onclick = async () => {
            bOne.disabled = true;
            const { members, subs } = await fetchNode(d.path, (s) => (bOne.textContent = s));
            log(`部門 ${d.name}（僅本層）：${members.size} 位成員${subs.size ? `，另有 ${subs.size} 個子部門未展開` : ""}。`);
            flushNotes(log);
            await addMany([...members.keys()], log); refreshAtt(); bOne.textContent = "已加入";
          };
          bar.appendChild(bAll); bar.appendChild(bOne);
          row.appendChild(bar); res.appendChild(row);
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
    $("#gb-mode").addEventListener("change", () => { res.style.display = "none"; res.innerHTML = ""; $("#gb-q").placeholder = $("#gb-mode").value === "person" ? "打名字，會即時跳建議" : "打部門代碼後按搜尋"; });
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

  // 測試掛鉤：Tampermonkey 環境沒有 module，這段不會做任何事；node 測試靠它拿到內部函式，
  // 才不必為了可測性把檔案拆成模組（那會犧牲「單檔貼上安裝」）。見 tests/test_group_book.mjs
  if (typeof module === "object" && module && module.exports) {
    module.exports = {
      esc, existing, attendeeInput, addOne, addMany,
      fetchRows, fetchNode, fetchSubs, loadDepts, loadDeptsAll, collectSubtree,
      NOTES, flushNotes,
      rootDirs, PAGE_SIZE, MAX_PAGES, MAX_NODES, ADD_TIMEOUT_MS, ADD_POLL_MS, MAX_FAIL_STREAK,
      _reset: () => { GSSABID = ""; ROOTS = null; TOPDEPTS = null; ALLDEPTS = null; SCOPE = null; NOTES.length = 0; },
      _abid: () => GSSABID,
    };
    return;   // 測試環境不要啟動 UI 輪詢
  }

  const iv = setInterval(() => { if (document.body) { clearInterval(iv); buildPanel(); } }, 800);
})();
