// ==UserScript==
// @name         m2k 行事曆助手 (Mail2000 Calendar Assistant)
// @namespace    gss.m2k.calendar
// @version      1.0.0
// @description  Mail2000 會議排程一站式面板：搜人/搜部門(遞迴)/貼 email 批次加與會者、查看所有與會者的忙碌時段與共同空檔（不必對方分享）、一鍵建立；另附多人行事曆看板。同源、沿用登入、免 CORS、免 token。
// @match        https://mail.gss.com.tw/cgi-bin/cal/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==
/*
 安裝：Tampermonkey（新版 Chrome 需到 chrome://extensions → Tampermonkey → 詳細資料
       → 開「允許使用者指令碼」）→ 新增腳本、貼上、存檔 → 重整行事曆頁。
 用法：右下「🗓 m2k 助手」開面板，兩個頁籤：
   【排會議】（在「會議排程」頁使用）
     A. 填會議資訊（標題/日期/開始/結束/地點）
     B. 加入與會者：① 搜姓名 ② 搜部門（遞迴展開該部門與所有子部門成員） ③ 貼 email
     🕒 查看空檔：自己 + 所有與會者，一人一列的週時間軸，頂端疊「共同空檔」；點空檔直接填回 A
     C. 按「✅ 建立會議」→ 自動填入原生表單、勾寄送通知信、儲存（存前會再確認一次）
   【看板】勾自己/他人/公用行事曆，或用姓名/部門/email 加人（不必對方分享），合併成每天一欄的看板。
 資料來源：
   - 他人行事曆走排程端點 /cgi-bin/cal/calsrv/schedule/{email}/instances，對全公司開放、只讀。
   - 已分享/公用行事曆走 feeds 端點。兩者的 dtstart/dtend 都直接是 epoch 秒，不必再加 offset。
 註：搜「部門」才會展開群組；直接打群組信箱(如 xxx@example.com)只是一個收件者、不會展開。
*/
(function () {
  "use strict";
  const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // 伺服器回來的姓名/部門/標題塞回 innerHTML 前必須跳脫
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const _URL = /(https?:\/\/[^\s<>"')\]]+)/g;
  const linkify = (t) => (t || "").split(_URL).map((p, i) =>
    i % 2 ? `<a href="${esc(p)}" target="_blank" rel="noopener">${esc(p)}</a>` : esc(p).replace(/\n/g, "<br>")
  ).join("");
  const WK = "一二三四五六日";
  const pad2 = (n) => String(n).padStart(2, "0");
  const ymd = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  const hhmm = (d) => `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  const stripMailto = (s) => String(s || "").replace(/^mailto:/i, "").trim().toLowerCase();
  async function getJSON(url) {
    const r = await fetch(url, { credentials: "include" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }
  // 併發上限：一人一支請求，與會者多時別一次全打出去
  async function mapLimit(items, limit, fn) {
    const out = new Array(items.length);
    let next = 0;
    const worker = async () => {
      while (next < items.length) { const i = next++; out[i] = await fn(items[i], i); }
    };
    await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
    return out;
  }

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
  // 逐一加入是序列的、中途無法取消，人數一多就是好幾分鐘，而原生表單能吃多少
  // 與會者也未知。所以超過門檻先問一聲，並給「只看名單」的出口。
  function confirmScale(n, log) {
    if (n <= BIG_ADD) return true;
    const secs = Math.ceil(n * 0.6);   // 每人約 0.5–1 秒（含 widget 回應）
    const mins = Math.floor(secs / 60), rest = secs % 60;
    const eta = mins ? `${mins} 分 ${rest} 秒` : `${secs} 秒`;
    const ok = confirm(
      `要一次加入 ${n} 位與會者。\n\n` +
      `預估耗時約 ${eta}，過程中無法取消，且原生表單對與會者人數的上限未知。\n\n` +
      `按「確定」開始加入；按「取消」則只把名單印在下方 log（可自行複製）。`);
    if (!ok) log("已取消加入，只列名單。");
    return ok;
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
    if (!confirmScale(todo.length, log)) {
      log(todo.join(", "));   // 印出來讓使用者自己複製
      return 0;
    }
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
  const BIG_ADD = 200;      // 超過這麼多人就先問一聲（逐一加入很久且中途無法取消）
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
    return { emails: [...members.keys()], nodeCount: visited.size, names: members };
  }

  /* ---------- 他人行事曆：排程端點 ---------- */
  // /cgi-bin/cal/calsrv/schedule/{email}/instances 是原生「會議排程」頁查與會者用的端點：
  // 對全公司任何人都查得到完整事件（標題、主辦人、與會者及回覆狀態），不必對方分享。
  // 只吃 webmail cookie，所以只能在瀏覽器內用（見 docs/adr/0001）。
  const SCHED_BASE = "/cgi-bin/cal/calsrv/schedule/";
  const FETCH_LIMIT = 4;      // 同時最多幾支排程請求
  const MAX_PEOPLE = 30;      // 時間軸最多畫幾人（含自己）
  const WORK_START = 9, WORK_END = 18;   // 共同空檔只算工作時段（週一到週五）
  const VIEW_START = 8, VIEW_END = 19;   // 時間軸畫的範圍（比工作時段寬一點，早會看得到）
  const DEFAULT_DURATION_MIN = 60;
  // attendee_reply_status（實機樣本推得，live 探針會再驗）：0 未回覆、1 已接受、2 已拒絕、3 暫定
  const REPLY = { 0: "needs", 1: "accepted", 2: "declined", 3: "tentative" };
  const schedUrl = (email, st, et) =>
    SCHED_BASE + encodeURIComponent(email) + "/instances?starttime=" + st + "&endtime=" + et;

  // 該人在這場事件的回覆狀態；沒在與會者名單（自建、無與會者）視為 own
  function replyStatusOf(inst, who) {
    const w = stripMailto(who);
    let att = inst.attendee;
    if (typeof att === "string") { try { att = JSON.parse(att); } catch (_) { att = []; } }
    if (!Array.isArray(att)) return "own";
    const me = att.find((a) => stripMailto(a && a.attendee) === w);
    if (!me) return "own";
    return REPLY[me.attendee_reply_status] || "needs";
  }
  // 忙碌判定（CONTEXT.md「忙碌時段」）：已拒絕不算；暫定算但另標；跨 24 小時以上視為請假/不在；其餘皆忙
  function busyKind(status, start, end) {
    if (status === "declined") return null;
    if (status === "tentative") return "tentative";
    if (end - start >= 86400e3) return "absent";
    return "busy";
  }
  // dtstart/dtend 直接是 epoch 秒。offset 欄位不要加：加了會把 19:00 的事件推到隔天 03:00，
  // 而且它有時是字串 "28800"，JS 一加就變字串串接。
  function toEvent(inst, who) {
    const start = new Date(Number(inst.dtstart) * 1000);
    const end = new Date(Number(inst.dtend || inst.dtstart) * 1000);
    const status = replyStatusOf(inst, who);
    let att = inst.attendee;
    if (typeof att === "string") { try { att = JSON.parse(att); } catch (_) { att = []; } }
    return {
      start, end, status, hasEnd: !!inst.dtend,
      kind: busyKind(status, start, end),
      summary: inst.summary || "(無標題)",
      organizer: stripMailto(inst.organizer),
      loc: inst.where || inst.location || "",
      desc: (inst.info && typeof inst.info === "object" && (inst.info.description || inst.info.content)) || inst.description || "",
      attCount: Array.isArray(att) ? att.length : 0,
    };
  }
  async function fetchSchedule(email, st, et) {
    try {
      const j = await getJSON(schedUrl(email, st, et));
      if (j.rspCode !== undefined && Number(j.rspCode) !== 0) {
        // -102 = 查無此帳號（實測；rspMsg 是空的）
        const why = Number(j.rspCode) === -102 ? "查無此帳號" : `rspCode ${j.rspCode}${j.rspMsg ? " " + j.rspMsg : ""}`;
        return { email, events: [], error: why };
      }
      return { email, events: (j.instances || []).map((i) => toEvent(i, email)) };
    } catch (e) { return { email, events: [], error: e.message }; }
  }

  // 一天的共同空檔：把所有人「算忙碌」的區間合併，從工作時段扣掉，留下長度夠的
  function commonFreeSlots(peopleEvents, day, durationMin) {
    const ws = new Date(day); ws.setHours(WORK_START, 0, 0, 0);
    const we = new Date(day); we.setHours(WORK_END, 0, 0, 0);
    const busy = [];
    for (const evs of peopleEvents) for (const ev of evs) {
      if (!ev.kind) continue;
      if (ev.end <= ws || ev.start >= we) continue;
      busy.push([Math.max(+ev.start, +ws), Math.min(+ev.end, +we)]);
    }
    busy.sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const [s, e] of busy) {
      if (merged.length && s <= merged[merged.length - 1][1]) merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], e);
      else merged.push([s, e]);
    }
    const out = [];
    let cur = +ws;
    for (const [s, e] of merged) {
      if (s > cur) out.push([cur, s]);
      cur = Math.max(cur, e);
    }
    if (cur < +we) out.push([cur, +we]);
    return out.filter(([s, e]) => e - s >= durationMin * 60e3).map(([s, e]) => ({ start: new Date(s), end: new Date(e) }));
  }
  // 該日期所在的週一 00:00 起五個工作日
  function workWeek(dateStr) {
    const [y, m, d] = (dateStr || ymd(new Date())).split("-").map(Number);
    const base = new Date(y, m - 1, d);
    if (isNaN(base)) return workWeek(ymd(new Date()));
    const mon = new Date(base); mon.setDate(base.getDate() - ((base.getDay() + 6) % 7)); mon.setHours(0, 0, 0, 0);
    return Array.from({ length: 5 }, (_, i) => { const x = new Date(mon); x.setDate(mon.getDate() + i); return x; });
  }
  function durationFromForm(st, et) {
    if (!st || !et) return DEFAULT_DURATION_MIN;
    const [sh, sm] = st.split(":").map(Number), [eh, em] = et.split(":").map(Number);
    const mins = (eh * 60 + em) - (sh * 60 + sm);
    return mins > 0 ? mins : DEFAULT_DURATION_MIN;
  }
  // 自己的 email：排程端點要用 email 當 key，而 feeds 清單裡沒有任何 email（實測欄位只有 id/display_name/url…），
  // 所以第一次直接問一次、記在這台瀏覽器；工具列有「不是我？」可重設。
  let SELF = null;
  function selfEmail() {
    if (SELF) return SELF;
    try { SELF = localStorage.getItem("m2k.selfEmail") || null; } catch (_) {}
    if (SELF) return SELF;
    const v = prompt("請輸入你的 Mail2000 email（用來查你自己的行程，只記在這台瀏覽器；之後可按「不是我？」更改）：") || "";
    SELF = (v.match(EMAIL_RE) || [null])[0];
    if (SELF) { SELF = SELF.toLowerCase(); try { localStorage.setItem("m2k.selfEmail", SELF); } catch (_) {} }
    return SELF;
  }
  function resetSelf() { SELF = null; try { localStorage.removeItem("m2k.selfEmail"); } catch (_) {} }

  // 時間軸 HTML：第一列共同空檔、第二列自己、其後每位與會者；每格是 08–19 的橫向帶
  function renderTimeline(people, days, durationMin, missing) {
    const span = (VIEW_END - VIEW_START) * 3600e3;
    const pct = (t, day) => { const base = new Date(day); base.setHours(VIEW_START, 0, 0, 0); return Math.max(0, Math.min(100, (t - base) / span * 100)); };
    const block = (s, e, cls, title, day, extra = "") => {
      const l = pct(s, day), r = pct(e, day);
      if (r <= l) return "";
      return `<div class="tl-blk tl-${cls}" style="left:${l.toFixed(2)}%;width:${(r - l).toFixed(2)}%" title="${esc(title)}"${extra ? " " + extra : ""}>${esc(title)}</div>`;
    };
    const md = (d) => `${d.getMonth() + 1}/${d.getDate()}`;
    // 跨日事件在中間日欄的標題要寫當日裁切後的區間，不是原始起訖
    const evTitle = (ev, ds, de) => ev.kind === "absent"
      ? `請假/不在（${md(ev.start)}–${md(new Date(+ev.end - 1))}）${ev.summary}`   // 結束在午夜整點時，最後一天是前一天
      : `${hhmm(ev.start < ds ? ds : ev.start)}–${hhmm(ev.end > de ? de : ev.end)} ${ev.summary}`;
    const ticks = Array.from({ length: VIEW_END - VIEW_START + 1 }, (_, i) => VIEW_START + i)
      .map((h) => `<span class="tl-tick" style="left:${((h - VIEW_START) / (VIEW_END - VIEW_START) * 100).toFixed(2)}%">${h}</span>`).join("");
    const head = `<tr><th class="tl-who"></th>${days.map((d) => {
      const today = ymd(d) === ymd(new Date());
      return `<th class="${today ? "tl-today" : ""}">${d.getMonth() + 1}/${d.getDate()} 週${WK[(d.getDay() + 6) % 7]}<div class="tl-ticks">${ticks}</div></th>`;
    }).join("")}</tr>`;
    const allEvents = people.map((p) => p.events);
    // 有人查不到、或被人數上限截掉 → 「大家都有空」不成立：空檔改畫灰色、不可點，免得把「查不到」當成「有空」
    const unknown = people.filter((p) => p.error).length + missing.length;
    const freeRow = `<tr class="tl-free"><td class="tl-who">共同空檔<br><small>≥ ${durationMin} 分${unknown ? `<br><span class="tl-err">${unknown} 人未計入，不可信</span>` : ""}</small></td>${days.map((d) => {
      const slots = commonFreeSlots(allEvents, d, durationMin);
      const inner = slots.map((s) => unknown
        ? block(s.start, s.end, "unsure", `${hhmm(s.start)}–${hhmm(s.end)}（${unknown} 人未計入）`, d)
        : block(s.start, s.end, "free", `${hhmm(s.start)}–${hhmm(s.end)}`, d,
          `data-date="${ymd(d)}" data-start="${hhmm(s.start)}" data-end="${hhmm(s.end)}"`)).join("");
      return `<td><div class="tl-band">${inner || '<span class="tl-none">—</span>'}</div></td>`;
    }).join("")}</tr>`;
    const rows = people.map((p) => `<tr><td class="tl-who" title="${esc(p.email)}">${esc(p.name || p.email)}${p.self ? " <small>(我)</small>" : ""}${p.error ? `<br><small class="tl-err">查不到：${esc(p.error)}</small>` : ""}</td>${days.map((d) => {
      const ds = new Date(d); ds.setHours(0, 0, 0, 0); const de = new Date(d); de.setHours(24, 0, 0, 0);
      const inner = p.events.filter((ev) => ev.kind && ev.end > ds && ev.start < de)
        .map((ev) => block(ev.start, ev.end, ev.kind, evTitle(ev, ds, de), d)).join("");
      return `<td><div class="tl-band">${inner}</div></td>`;
    }).join("")}</tr>`).join("");
    const note = missing.length ? `<p class="tl-note">⚠ 與會者超過 ${MAX_PEOPLE} 人，時間軸只畫前 ${MAX_PEOPLE} 位，共同空檔不含他們；未列入：${esc(missing.join(", "))}</p>` : "";
    return `${note}<table class="tl">${head}${freeRow}${rows}</table>
      <p class="tl-legend"><span class="tl-blk tl-busy">忙碌</span> <span class="tl-blk tl-tentative">暫定</span> <span class="tl-blk tl-absent">請假/不在</span> <span class="tl-blk tl-free">共同空檔（點一下填回表單）</span> <span class="tl-blk tl-unsure">有人查不到時的空檔（不可點）</span>　已拒絕的邀請不算忙碌。</p>`;
  }

  /* ---------- 看板：feeds 端點（自己 / 已分享 / 公用） ---------- */
  const FEEDS_BASE = "/cgi-bin/cal/calsrv/feeds/default/";
  const hexColor = (n) => "#" + ("000000" + ((n >>> 0) & 0xffffff).toString(16)).slice(-6);
  // 給用 email 加進看板的人一個穩定顏色
  const colorFor = (s) => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0; return `hsl(${h % 360} 55% 42%)`; };

  async function listCalendars() {
    const cals = [];
    for (const type of ["default", "subscribed", "public"]) {
      try {
        const j = await getJSON(FEEDS_BASE + type + "/");
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
  async function fetchFeedEvents(cal, st, et) {
    const url = FEEDS_BASE + cal.feeds + "/" + cal.id + "/events/instances/?starttime=" + st + "&endtime=" + et;
    let j;
    try { j = await getJSON(url); } catch (e) { return []; }
    return (j.instances || []).map((e) => ({ ...toEvent(e, ""), calName: cal.name, color: cal.color }));
  }

  function renderBoard(container, events, dayCount, startDate) {
    const byDay = new Map();
    for (let i = 0; i < dayCount; i++) {
      const d = new Date(startDate); d.setDate(d.getDate() + i);
      byDay.set(d.toDateString(), { date: new Date(d), items: [] });
    }
    events.sort((a, b) => a.start - b.start);
    for (const ev of events) {
      // 跨日事件放進它涵蓋的每一天，不只開始那天（結束在午夜整點的，不算進那天）
      const d = new Date(ev.start); d.setHours(0, 0, 0, 0);
      const last = new Date(+ev.end - 1);
      for (; d <= last; d.setDate(d.getDate() + 1)) {
        const k = d.toDateString();
        if (byDay.has(k)) byDay.get(k).items.push(ev);
      }
    }
    const cols = [];
    for (const { date, items } of byDay.values()) {
      const today = date.toDateString() === new Date().toDateString();
      const cards = items.map((ev) => {
        const t = ev.hasEnd ? `${hhmm(ev.start)}–${hhmm(ev.end)}` : hhmm(ev.start);
        const meta = [];
        if (ev.loc) meta.push(`<div class="mb-loc">📍 ${linkify(ev.loc)}</div>`);
        if (ev.organizer) meta.push(`<div class="mb-meta">👤 ${esc(ev.organizer)}</div>`);
        if (ev.attCount) meta.push(`<div class="mb-meta">👥 ${ev.attCount} 人</div>`);
        const desc = (ev.desc && ev.desc.trim())
          ? `<details class="mb-desc"><summary></summary><div class="mb-descbody">${linkify(ev.desc)}</div></details>` : "";
        return `<div class="mb-card" style="border-left-color:${ev.color}">
          <div class="mb-cal"><span class="mb-dot" style="background:${ev.color}"></span>${esc(ev.calName)}</div>
          <div class="mb-time">${t}</div><div class="mb-title">${linkify(ev.summary)}</div>
          ${meta.join("")}${desc}</div>`;
      }).join("") || '<div class="mb-empty">—</div>';
      cols.push(`<div class="mb-col${today ? " mb-today" : ""}">
        <div class="mb-hdr">${date.getMonth() + 1}/${date.getDate()} <span>週${WK[(date.getDay() + 6) % 7]}</span>${today ? " · 今天" : ""}</div>
        <div class="mb-cards">${cards}</div></div>`);
    }
    container.innerHTML = `<div class="mb-board">${cols.join("")}</div>`;
  }

  /* ---------- 填原生表單 + 儲存 ---------- */
  function setNative(id, val) { const el = document.getElementById(id); if (!el) return false; el.value = val; el.dispatchEvent(new Event("input", { bubbles: true })); el.dispatchEvent(new Event("change", { bubbles: true })); return true; }
  function setDate(id, ymdStr) {
    const [y, m, d] = ymdStr.split("-").map(Number); const el = document.getElementById(id);
    try { if (window.jQuery && jQuery(el).datepicker) { jQuery(el).datepicker("setDate", new Date(y, m - 1, d)); return true; } } catch (e) {}
    return setNative(id, y + "/" + String(m).padStart(2, "0") + "/" + String(d).padStart(2, "0"));
  }

  /* ---------- UI ---------- */
  const CSS = `
    #m2k-btn{position:fixed;right:18px;bottom:18px;z-index:999999;padding:10px 14px;background:#2563eb;color:#fff;border:none;border-radius:8px;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.25);font-size:14px}
    #m2k-panel{position:fixed;right:18px;bottom:64px;z-index:999999;width:380px;background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;box-shadow:0 6px 24px rgba(0,0,0,.2);font:13px/1.5 -apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;display:none;max-height:84vh;overflow:auto;color:#0f172a}
    #m2k-panel input,#m2k-panel select,#m2k-panel textarea{font:inherit}
    .m2k-tabs{display:flex;gap:4px;margin-bottom:8px;border-bottom:1px solid #e2e8f0}
    .m2k-tab{padding:6px 12px;cursor:pointer;border:none;background:none;color:#64748b;border-bottom:2px solid transparent;font-weight:600}
    .m2k-tab.on{color:#2563eb;border-bottom-color:#2563eb}
    .m2k-sec{color:#475569;margin:6px 0 2px}
    .m2k-btn{padding:4px 8px;background:#2563eb;color:#fff;border:none;border-radius:6px;cursor:pointer}
    .m2k-btn.green{background:#16a34a}.m2k-btn.grey{background:#fff;color:#334155;border:1px solid #cbd5e1}.m2k-btn.wide{width:100%;padding:5px}
    .m2k-res{margin:5px 0;max-height:150px;overflow:auto;border:1px solid #e2e8f0;border-radius:6px;padding:4px;display:none}
    .m2k-row{display:flex;justify-content:space-between;align-items:center;gap:4px;padding:3px 4px;border-radius:4px}
    .m2k-row:hover{background:#f1f5f9}
    .m2k-log{margin-top:8px;max-height:110px;overflow:auto;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:6px;white-space:pre-wrap;color:#334155}
    .m2k-chip{display:inline-flex;align-items:center;gap:4px;background:#f1f5f9;border-radius:12px;padding:1px 8px;margin:2px;font-size:12px}
    .m2k-chip b{cursor:pointer;color:#94a3b8}
    #m2k-overlay{position:fixed;inset:0;z-index:1000000;background:rgba(15,23,42,.5);display:none}
    #m2k-modal{position:absolute;inset:24px;background:#f1f5f9;border-radius:12px;display:flex;flex-direction:column;overflow:hidden;font-family:-apple-system,"PingFang TC","Microsoft JhengHei",sans-serif;color:#0f172a}
    #m2k-mtop{display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:12px 16px;background:#fff;border-bottom:1px solid #e2e8f0}
    #m2k-mtop b{font-size:15px}
    #m2k-mbody{flex:1;overflow:auto;padding:14px}
    .mb-x{border:none;background:#e2e8f0;border-radius:6px;padding:6px 10px;cursor:pointer}
    .tl{border-collapse:separate;border-spacing:0 4px;width:100%;table-layout:fixed;font-size:12px}
    .tl th{background:#334155;color:#fff;padding:6px 6px 18px;font-weight:600;position:relative;text-align:left}
    .tl th.tl-today{background:#1d4ed8}
    .tl th.tl-who,.tl td.tl-who{width:150px;background:#fff;color:#0f172a;vertical-align:middle;padding:4px 8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .tl th.tl-who{background:transparent}
    .tl-ticks{position:absolute;left:6px;right:6px;bottom:2px;height:12px}
    .tl-tick{position:absolute;transform:translateX(-50%);font-size:9px;opacity:.75;font-weight:400}
    .tl td{background:#fff;padding:4px 6px;vertical-align:middle}
    .tl-band{position:relative;height:26px;background:repeating-linear-gradient(90deg,#f1f5f9 0,#f1f5f9 1px,transparent 1px,transparent 9.0909%);border-radius:4px;overflow:hidden}
    .tl-blk{position:absolute;top:2px;bottom:2px;border-radius:3px;font-size:10px;line-height:22px;padding:0 4px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;color:#fff}
    .tl-busy{background:#2563eb}
    .tl-tentative{background:repeating-linear-gradient(45deg,#60a5fa 0,#60a5fa 4px,#bfdbfe 4px,#bfdbfe 8px);color:#1e3a8a}
    .tl-absent{background:#64748b}
    .tl-free{background:#16a34a;cursor:pointer}
    .tl-free:hover{background:#15803d}
    .tl-free .tl-band{background:#ecfdf5}
    .tl-unsure{background:repeating-linear-gradient(45deg,#cbd5e1 0,#cbd5e1 4px,#e2e8f0 4px,#e2e8f0 8px);color:#475569;cursor:not-allowed}
    .tl-none{color:#94a3b8;font-size:11px;line-height:26px;padding-left:6px}
    .tl-err{color:#dc2626}
    .tl-note{color:#b45309;margin:0 0 8px}
    .tl-legend{color:#475569;font-size:12px;margin-top:8px}
    .tl-legend .tl-blk{position:static;display:inline-block;line-height:18px;margin-right:4px}
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
    #m2k-cals label{font-size:13px;display:flex;align-items:center;gap:4px;cursor:pointer}
    .mb-pub>summary{cursor:pointer;color:#0891b2;font-size:12px}`;

  function buildPanel() {
    if (document.getElementById("m2k-btn")) return;
    const style = document.createElement("style"); style.textContent = CSS; document.head.appendChild(style);
    const btn = document.createElement("button");
    btn.id = "m2k-btn"; btn.textContent = "🗓 m2k 助手";
    document.body.appendChild(btn);
    const panel = document.createElement("div"); panel.id = "m2k-panel";
    panel.innerHTML = `
      <div class="m2k-tabs"><button class="m2k-tab on" data-tab="book">排會議</button><button class="m2k-tab" data-tab="board">看板</button></div>
      <div id="m2k-tab-book">
        <div class="m2k-sec">A. 會議資訊</div>
        <input id="gb-title" placeholder="會議標題" style="width:100%;box-sizing:border-box;margin-bottom:4px">
        <div style="display:flex;gap:4px;margin-bottom:4px">
          <input id="gb-date" type="date" style="flex:1;min-width:0">
          <input id="gb-start" type="time" step="600" style="width:90px">
          <input id="gb-end" type="time" step="600" style="width:90px">
        </div>
        <input id="gb-loc" placeholder="地點（可空）" style="width:100%;box-sizing:border-box">
        <div id="gb-att" style="color:#16a34a;font-size:12px;margin:4px 0">目前與會者：0 人</div>

        <div class="m2k-sec">B. 加入與會者</div>
        <div style="display:flex;gap:4px">
          <select id="gb-mode" style="width:96px"><option value="person">搜姓名</option><option value="dept">搜部門</option></select>
          <input id="gb-q" placeholder="人名 / 部門代碼" style="flex:1;min-width:0">
          <button id="gb-go" class="m2k-btn">搜尋</button>
        </div>
        <div id="gb-res" class="m2k-res"></div>
        <details style="margin-top:6px"><summary style="cursor:pointer;color:#64748b">貼上 email 批次加入</summary>
          <textarea id="gb-paste" rows="3" style="width:100%;box-sizing:border-box;margin-top:4px" placeholder="a@example.com&#10;b@example.com"></textarea>
          <button id="gb-addpaste" class="m2k-btn green wide">加入貼上的</button>
        </details>

        <button id="gb-slots" class="m2k-btn wide" style="margin-top:10px;padding:8px;background:#0891b2">🕒 查看與會者空檔</button>
        <button id="gb-book" class="m2k-btn wide" style="padding:9px;margin-top:6px;background:#7c3aed;border-radius:8px;font-weight:600">✅ 建立會議</button>
        <div id="gb-log" class="m2k-log"></div>
      </div>
      <div id="m2k-tab-board" style="display:none">
        <div class="m2k-sec">行事曆（自己 / 已分享 / 公用）</div>
        <div id="m2k-cals" style="display:flex;gap:6px 10px;flex-wrap:wrap">載入中…</div>
        <div class="m2k-sec" style="margin-top:8px">加人（不必對方分享）</div>
        <div style="display:flex;gap:4px">
          <select id="bd-mode" style="width:96px"><option value="person">搜姓名</option><option value="dept">搜部門</option><option value="email">貼 email</option></select>
          <input id="bd-q" placeholder="人名 / 部門代碼 / email" style="flex:1;min-width:0">
          <button id="bd-go" class="m2k-btn">加入</button>
        </div>
        <div id="bd-res" class="m2k-res"></div>
        <div id="bd-people" style="margin:4px 0"></div>
        <div style="display:flex;gap:8px;align-items:center;margin-top:8px">
          <label>從 <input id="bd-from" type="date" style="width:130px"></label>
          <label>天數 <input id="bd-days" type="number" value="7" min="1" max="31" style="width:56px"></label>
          <button id="bd-gen" class="m2k-btn" style="margin-left:auto">產生看板</button>
        </div>
        <div id="bd-log" class="m2k-log"></div>
      </div>`;
    document.body.appendChild(panel);
    const ov = document.createElement("div"); ov.id = "m2k-overlay";
    ov.innerHTML = `<div id="m2k-modal"><div id="m2k-mtop"><b id="m2k-mtitle"></b><span id="m2k-mtools" style="display:flex;gap:6px;align-items:center;flex:1"></span><button class="mb-x" id="m2k-mclose">關閉</button></div><div id="m2k-mbody"></div></div>`;
    document.body.appendChild(ov);
    const $ = (s) => panel.querySelector(s);
    const $$ = (s) => ov.querySelector(s);
    const mkLog = (el) => (m) => { el.textContent += (el.textContent ? "\n" : "") + m; el.scrollTop = el.scrollHeight; };
    const log = mkLog($("#gb-log")), blog = mkLog($("#bd-log"));
    const openModal = (title) => { $$("#m2k-mtitle").textContent = title; $$("#m2k-mtools").innerHTML = ""; $$("#m2k-mbody").innerHTML = '<p style="padding:12px;color:#64748b">讀取中…</p>'; ov.style.display = "block"; };
    const closeModal = () => (ov.style.display = "none");
    $$("#m2k-mclose").onclick = closeModal;
    ov.onclick = (e) => { if (e.target === ov) closeModal(); };

    // 頁籤
    panel.querySelectorAll(".m2k-tab").forEach((t) => t.onclick = () => {
      panel.querySelectorAll(".m2k-tab").forEach((x) => x.classList.toggle("on", x === t));
      $("#m2k-tab-book").style.display = t.dataset.tab === "book" ? "" : "none";
      $("#m2k-tab-board").style.display = t.dataset.tab === "board" ? "" : "none";
      if (t.dataset.tab === "board") loadCalList();
    });
    const refreshAtt = () => { $("#gb-att").textContent = `目前與會者：${existing().size} 人`; };
    setInterval(refreshAtt, 1500);
    // 初始靠 CSS 隱藏、inline style 是空字串，所以用「是否已開」判斷，不能用 === "none"
    btn.onclick = () => { panel.style.display = panel.style.display === "block" ? "none" : "block"; refreshAtt(); };

    /* ---- 共用：搜人 / 搜部門的結果清單。onPick(emails, names) 決定加到哪 ---- */
    function personRows(res, list, isAdded, onPick) {
      res.innerHTML = "";
      list.forEach((p) => {
        const added = isAdded(p.email.toLowerCase());
        const row = document.createElement("div"); row.className = "m2k-row"; row.style.cursor = "pointer";
        row.innerHTML = `<span>${esc(p.name)} <span style="color:#94a3b8">&lt;${esc(p.email)}&gt;</span></span>`;
        const tag = document.createElement("span");
        tag.textContent = added ? "✓ 已加" : "＋ 加入";
        tag.style.cssText = "font-size:12px;white-space:nowrap;margin-left:6px;color:" + (added ? "#16a34a" : "#2563eb");
        row.appendChild(tag);
        if (!added) row.onclick = async () => { row.onclick = null; await onPick([p.email], new Map([[p.email.toLowerCase(), p.name]])); tag.textContent = "✓ 已加"; tag.style.color = "#16a34a"; };
        res.appendChild(row);
      });
    }
    async function personLive(res, q, seqRef, isAdded, onPick) {
      const seq = ++seqRef.n;
      res.style.display = "block"; res.innerHTML = "搜尋中…";
      try {
        const list = await searchPeople(q);
        if (seq !== seqRef.n) return; // 已有更新的查詢，捨棄舊結果
        if (!list.length) { res.innerHTML = "查無此人。"; return; }
        personRows(res, list, isAdded, onPick);
      } catch (e) { res.innerHTML = "搜尋失敗：" + esc(e.message); }
    }
    async function deptSearch(res, q, logFn, onPick) {
      res.style.display = "block"; res.innerHTML = "展開部門樹…";
      try {
        const ql = q.toLowerCase();
        const hit = (list) => list.filter((d) => (d.name + d.path).toLowerCase().includes(ql));
        let matches = hit(await loadDepts((s) => (res.textContent = s)));
        // 頂層沒中才搜整棵樹：深層的子部門代碼只有全樹找得到
        if (!matches.length) {
          res.textContent = "頂層沒有，改搜整棵部門樹（第一次會花點時間）…";
          matches = hit(await loadDeptsAll((s) => (res.textContent = s)));
        }
        flushNotes(logFn);
        if (!matches.length) { res.innerHTML = "查無此部門。請用通訊錄裡的部門代碼。（純郵件群組無法展開）"; return; }
        res.innerHTML = "";
        matches.forEach((d) => {
          const row = document.createElement("div"); row.className = "m2k-row";
          row.innerHTML = `<span>${esc(d.name)} <span style="color:#94a3b8">${esc(d.path)}</span></span>`;
          const bar = document.createElement("span"); bar.style.cssText = "display:flex;gap:4px;white-space:nowrap";
          // 主動作＝含子部門（要「整個部門的人」時的預設；子部門數要展開後才知道）
          const bAll = document.createElement("button"); bAll.textContent = "展開全部並加入"; bAll.className = "m2k-btn green";
          bAll.onclick = async () => {
            bAll.disabled = true;
            const { emails, nodeCount, names } = await collectSubtree(d.path, (s) => (bAll.textContent = s));
            logFn(`部門 ${d.name}：${nodeCount} 個部門、${emails.length} 位成員。`);
            flushNotes(logFn);
            await onPick(emails, names); bAll.textContent = "已加入";
          };
          const bOne = document.createElement("button"); bOne.textContent = "僅本層"; bOne.className = "m2k-btn grey";
          bOne.onclick = async () => {
            bOne.disabled = true;
            const { members, subs } = await fetchNode(d.path, (s) => (bOne.textContent = s));
            logFn(`部門 ${d.name}（僅本層）：${members.size} 位成員${subs.size ? `，另有 ${subs.size} 個子部門未展開` : ""}。`);
            flushNotes(logFn);
            await onPick([...members.keys()], members); bOne.textContent = "已加入";
          };
          bar.appendChild(bAll); bar.appendChild(bOne);
          row.appendChild(bar); res.appendChild(row);
        });
      } catch (e) { res.innerHTML = "失敗：" + esc(e.message); }
    }
    // 把「輸入框 + 模式 + 搜尋鈕」接起來：姓名模式邊打邊搜（debounce），部門模式按鈕搜
    function wireSearch({ q, mode, go, res, logFn, isAdded, onPick, onEmail }) {
      const seqRef = { n: 0 }; let acTimer = null;
      const run = () => {
        const v = q.value.trim(); if (!v) { res.style.display = "none"; return; }
        const m = mode.value;
        if (m === "person") personLive(res, v, seqRef, isAdded, onPick);
        else if (m === "dept") deptSearch(res, v, logFn, onPick);
        else if (onEmail) onEmail(v);
      };
      go.onclick = run;
      q.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
      q.addEventListener("input", () => {
        clearTimeout(acTimer);
        if (mode.value !== "person") return;
        const v = q.value.trim();
        const minLen = /[一-龥]/.test(v) ? 1 : 2;           // 中文1字即搜，英文2字
        if (v.length < minLen) { res.style.display = "none"; return; }
        acTimer = setTimeout(() => personLive(res, v, seqRef, isAdded, onPick), 300);
      });
      mode.addEventListener("change", () => {
        res.style.display = "none"; res.innerHTML = "";
        q.placeholder = mode.value === "person" ? "打名字，會即時跳建議" : mode.value === "dept" ? "打部門代碼後按搜尋" : "貼 email，可多個";
      });
    }

    /* ---- 排會議頁籤 ---- */
    const NAMES = new Map();   // email → 通訊錄姓名（搜到的才有；時間軸左欄用）
    const remember = (names) => names && names.forEach((n, e) => { if (n) NAMES.set(e.toLowerCase(), n); });
    // 原生 chip 上的文字通常是姓名，撿來當備用
    const chipName = (email) => { const el = document.querySelector(`${SEL.list} ${SEL.item}[data-id="${email.replace(/["\\]/g, "\\$&")}" i]`); const t = el && (el.textContent || "").trim(); return t && !t.includes("@") ? t : ""; };
    wireSearch({
      q: $("#gb-q"), mode: $("#gb-mode"), go: $("#gb-go"), res: $("#gb-res"), logFn: log,
      isAdded: (e) => existing().has(e),
      onPick: async (emails, names) => { remember(names); await addMany(emails, log); refreshAtt(); },
    });
    $("#gb-addpaste").onclick = async () => { const em = $("#gb-paste").value.match(EMAIL_RE) || []; if (!em.length) return log("沒抓到 email。"); await addMany(em, log); refreshAtt(); };

    // 查看空檔：自己 + 目前與會者，一人一支排程請求，畫週時間軸
    let weekAnchor = null, renderSeq = 0;
    async function showSlots() {
      const seq = ++renderSeq;   // 載入中連按上/下週：只留最後一次的結果
      const me = await selfEmail();
      if (seq !== renderSeq) return;
      if (!me) return log("沒有你的 email，無法查你自己的行程。");
      const others = [...existing()].filter((e) => e !== me);
      const all = [me, ...others];
      const shown = all.slice(0, MAX_PEOPLE), missing = all.slice(MAX_PEOPLE);
      const durationMin = durationFromForm($("#gb-start").value, $("#gb-end").value);
      openModal("與會者空檔");
      const tools = $$("#m2k-mtools");
      tools.innerHTML = `<button class="mb-x" id="tl-prev">◀ 上週</button><span id="tl-range" style="font-weight:600"></span><button class="mb-x" id="tl-next">下週 ▶</button><span style="color:#64748b;font-size:12px">共 ${shown.length} 人　我：${esc(me)} <a href="#" id="tl-me" style="color:#2563eb">不是我？</a></span>`;
      const days = workWeek(weekAnchor || $("#gb-date").value);
      // 伺服器用區間重疊比對（live_schedule_probe A10 實測 PASS）：上週就開始、本週還沒結束的請假也會回，窗不必前推
      const st = Math.floor(+days[0] / 1000), et = Math.floor((+days[4] + 86400e3) / 1000);
      $$("#tl-range").textContent = `${ymd(days[0])} ～ ${ymd(days[4])}`;
      const go = (delta) => { const d = new Date(days[0]); d.setDate(d.getDate() + delta); weekAnchor = ymd(d); showSlots().catch((e) => log("查空檔失敗：" + e.message)); };
      $$("#tl-prev").onclick = () => go(-7);
      $$("#tl-next").onclick = () => go(7);
      $$("#tl-me").onclick = (e) => { e.preventDefault(); resetSelf(); showSlots().catch((err) => log("查空檔失敗：" + err.message)); };
      const results = await mapLimit(shown, FETCH_LIMIT, (e) => fetchSchedule(e, st, et));
      if (seq !== renderSeq) return;
      const people = results.map((r, i) => ({ email: r.email, name: NAMES.get(r.email) || chipName(r.email) || r.email.split("@")[0], self: i === 0, events: r.events, error: r.error }));
      const body = $$("#m2k-mbody");
      body.innerHTML = renderTimeline(people, days, durationMin, missing);
      body.querySelectorAll(".tl-free[data-date]").forEach((b) => b.onclick = () => {
        const s = b.dataset.start.split(":").map(Number);
        const endMin = s[0] * 60 + s[1] + durationMin;
        $("#gb-date").value = b.dataset.date; $("#gb-start").value = b.dataset.start;
        $("#gb-end").value = `${pad2(Math.floor(endMin / 60))}:${pad2(endMin % 60)}`;
        log(`已填入 ${b.dataset.date} ${b.dataset.start}–${$("#gb-end").value}（${durationMin} 分鐘）。`);
        closeModal();
      });
      const failed = people.filter((p) => p.error).length;
      if (failed) log(`⚠ ${failed} 位的行程查不到，共同空檔列已改為灰色不可點。`);
    }
    $("#gb-slots").onclick = () => { weekAnchor = null; showSlots().catch((e) => log("查空檔失敗：" + e.message)); };

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

    /* ---- 看板頁籤 ---- */
    let CALS = null, CALS_P = null;
    const PEOPLE = new Map();   // email → name（用排程端點查的人）
    function loadCalList() {   // 連續切頁籤/按產生只打一次；失敗不快取，下次再試
      return (CALS_P = CALS_P || loadCalListNow().catch((e) => { CALS_P = null; blog("讀不到行事曆清單：" + e.message); CALS = CALS || []; }));
    }
    async function loadCalListNow() {
      CALS = await listCalendars();
      const chk = (i, c, on) => `<label><input type="checkbox" data-i="${i}" ${on ? "checked" : ""}>` +
        `<span class="mb-dot" style="background:${c.color}"></span>${esc(c.name)}</label>`;
      const mine = [], subs = [], pubs = [];
      CALS.forEach((c, i) => (c.type === "default" ? mine : c.type === "subscribed" ? subs : pubs).push(chk(i, c, c.type !== "public")));
      $("#m2k-cals").innerHTML = (mine.join("") + subs.join("") || '<span style="color:#94a3b8">（沒有可用的行事曆）</span>') +
        (pubs.length ? `<details class="mb-pub" style="flex-basis:100%"><summary>公用 / 資源 (${pubs.length})</summary>
          <div style="display:flex;gap:6px 10px;flex-wrap:wrap;margin-top:6px">${pubs.join("")}</div></details>` : "");
    }
    function renderPeople() {
      const box = $("#bd-people");
      box.innerHTML = [...PEOPLE].map(([e, n]) => `<span class="m2k-chip" title="${esc(e)}"><span class="mb-dot" style="background:${colorFor(e)}"></span>${esc(n || e)} <b data-e="${esc(e)}">✕</b></span>`).join("") ||
        '<span style="color:#94a3b8;font-size:12px">尚未加人</span>';
      box.querySelectorAll("b[data-e]").forEach((b) => b.onclick = () => { PEOPLE.delete(b.dataset.e); renderPeople(); });
    }
    renderPeople();
    const addPeople = async (emails, names) => {
      let dropped = 0;
      emails.forEach((e) => {
        const k = e.toLowerCase();
        if (PEOPLE.has(k)) return;
        if (PEOPLE.size >= MAX_PEOPLE) { dropped++; return; }   // 看板同樣最多 30 人：一人一支請求 × 天數
        PEOPLE.set(k, (names && names.get(k)) || k.split("@")[0]);
      });
      if (dropped) blog(`⚠ 看板最多 ${MAX_PEOPLE} 人，${dropped} 位沒加入。`);
      renderPeople();
    };
    wireSearch({
      q: $("#bd-q"), mode: $("#bd-mode"), go: $("#bd-go"), res: $("#bd-res"), logFn: blog,
      isAdded: (e) => PEOPLE.has(e),
      onPick: addPeople,
      onEmail: (v) => { const em = v.match(EMAIL_RE) || []; if (!em.length) return blog("沒抓到 email。"); addPeople(em); $("#bd-q").value = ""; },
    });
    $("#bd-gen").onclick = async () => {
      const days = Math.max(1, Math.min(31, parseInt($("#bd-days").value) || 7));
      const from = $("#bd-from").value;
      const start = from ? new Date(from + "T00:00:00") : new Date(); start.setHours(0, 0, 0, 0);
      const end = new Date(start); end.setDate(end.getDate() + days);
      const st = Math.floor(+start / 1000), et = Math.floor(+end / 1000);
      await loadCalList();
      const chosen = [...$("#m2k-cals").querySelectorAll("input:checked")].map((c) => CALS[+c.dataset.i]);
      if (!chosen.length && !PEOPLE.size) return blog("請至少勾一個行事曆或加一個人。");
      openModal("多人行事曆看板");
      const all = (await mapLimit(chosen, FETCH_LIMIT, (cal) => fetchFeedEvents(cal, st, et))).flat();
      const results = await mapLimit([...PEOPLE.keys()], FETCH_LIMIT, (e) => fetchSchedule(e, st, et));
      results.forEach((r) => {
        if (r.error) blog(`⚠ ${r.email} 查不到：${r.error}`);
        const name = PEOPLE.get(r.email) || r.email, color = colorFor(r.email);
        // 看板是「看行程」不是「算忙碌」，已拒絕的也照樣列出，讓人知道對方被邀了但不去
        r.events.forEach((ev) => all.push({ ...ev, calName: name + (ev.status === "declined" ? "（已拒絕）" : ev.status === "tentative" ? "（暫定）" : ""), color }));
      });
      renderBoard($$("#m2k-mbody"), all, days, start);
    };
  }

  // 測試掛鉤：Tampermonkey 環境沒有 module，這段不會做任何事；node 測試靠它拿到內部函式，
  // 才不必為了可測性把檔案拆成模組（那會犧牲「單檔貼上安裝」）。見 tests/calendar.test.mjs
  if (typeof module === "object" && module && module.exports) {
    module.exports = {
      esc, existing, attendeeInput, addOne, addMany,
      fetchRows, fetchNode, fetchSubs, loadDepts, loadDeptsAll, collectSubtree,
      NOTES, flushNotes,
      rootDirs, BIG_ADD, PAGE_SIZE, MAX_PAGES, MAX_NODES, ADD_TIMEOUT_MS, ADD_POLL_MS, MAX_FAIL_STREAK,
      // 他人行事曆 / 時間軸
      schedUrl, replyStatusOf, busyKind, toEvent, fetchSchedule, commonFreeSlots, workWeek,
      durationFromForm, mapLimit, renderTimeline, fetchFeedEvents, selfEmail, resetSelf,
      FETCH_LIMIT, MAX_PEOPLE, WORK_START, WORK_END, VIEW_START, VIEW_END, DEFAULT_DURATION_MIN,
      _reset: () => { GSSABID = ""; ROOTS = null; TOPDEPTS = null; ALLDEPTS = null; SCOPE = null; SELF = null; NOTES.length = 0; },
      _abid: () => GSSABID,
    };
    return;   // 測試環境不要啟動 UI 輪詢
  }

  const iv = setInterval(() => { if (document.body) { clearInterval(iv); buildPanel(); } }, 800);
})();
