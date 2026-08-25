/* 實機迴歸測試 — 驗證 m2k-group-book.user.js 對 Mail2000 通訊錄(adb2) 的行為假設。
 *
 * 為什麼是這種形式：那支 userscript 的部門遞迴、分頁停止條件、加入成功判定，全部建立在
 * 「adb2 端點會怎麼回應」的假設上。這些在 node 裡測不到（沒 session、沒真實 HTML），
 * 用自己猜的 mock 寫單元測試只會測到自己的假設。所以改成問伺服器本人，且做成可重複跑。
 *
 * 用法（可重複，不限次數）：
 *   1. 在已登入的 Mail2000 開「會議排程」頁（F 組需要與會者欄位；沒有會 SKIP）
 *   2. DevTools Console 貼上本檔全文
 *   3. await adb2probe("<部門代碼>")            // 要驗哪個部門
 *      await adb2probe()                      // 不給就印出頂層部門讓你挑
 *      await adb2probe("<部門代碼>", { full: true })   // 加跑整棵部門樹（會慢）
 *      await adb2probe("<部門代碼>", { abid: "..." })  // 手動指定通訊錄
 *   4. 每項印 PASS / FAIL / SKIP；結束後 window.__adb2probe 有原始回應可複製
 *
 * 唯讀：只發 GET，不加/不刪與會者、不改任何狀態。F 組讀畫面上已存在的 chip。
 *
 * 註：這裡刻意重寫一份解析，而不是重用 userscript 的函式（它是 IIFE、什麼都不 export）。
 * 獨立實作也讓探針能驗證 production 的解析有沒有漏東西。
 *
 * 斷言抓的是「量級崩掉」（遞迴後人數不比本層多 = 遞迴壞了、0 人 = 解析壞了），
 * 不是精確數字 —— 人事與組織調整都會讓數字漂移，那不算迴歸。
 * 注意：輸出含真實通訊錄內容（信箱、姓名、部門結構），貼給他人前請自行斟酌。
 */
(function () {
  "use strict";
  const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/;
  const SEL = { list: ".scheduleAttendeeList", item: ".scheduleAttendee" };
  const PAGE_SIZE = 25, MAX_PAGES = 40, MAX_NODES = 300;

  const results = [];
  const say = (kind, name, msg) => {
    results.push({ kind, name, msg });
    const css = kind === "PASS" ? "color:#16a34a;font-weight:600"
      : kind === "FAIL" ? "color:#dc2626;font-weight:600" : "color:#94a3b8;font-weight:600";
    console.log("%c" + kind + "%c " + name + (msg ? " — " + msg : ""), css, "");
  };
  const check = (name, cond, msg) => say(cond ? "PASS" : "FAIL", name, msg);

  let calls = 0;
  const get = async (url) => {
    calls++;
    const r = await fetch(url, { credentials: "include" });
    const text = await r.text();
    return { status: r.status, text, doc: new DOMParser().parseFromString(text, "text/html") };
  };

  async function adb2probe(dept = "", opts = {}) {
    const raw = { dept, at: new Date().toISOString(), fixtures: {} };
    results.length = 0;
    console.log(`%c=== adb2 迴歸測試：${dept}${opts.full ? " (含全樹)" : ""} ===`, "font-weight:700;font-size:13px");

    /* ---- A. 通訊錄 abid ---- */
    let abid = opts.abid || "";
    const roots = [];
    try {
      const r = await get("/cgi-bin/adb2tree?tofield=widget");
      raw.fixtures["adb2tree"] = r.text;
      const books = [], seen = new Set(), re = /do_switchto\(\s*['"]([^'"]*)['"]\s*,\s*['"]([^'"]*)['"]/g;
      let m;
      while ((m = re.exec(r.text))) {
        if (seen.has(m[1] + "|" + m[2])) continue;
        seen.add(m[1] + "|" + m[2]);
        books.push({ abid: m[1], dirid: m[2] });
      }
      raw.books = books;
      check("A1 列出通訊錄", books.length > 0, books.map((b) => b.abid || "(空=個人)").join(" | "));
      // production 的取法：正則要求非空，所以會跳過個人通訊錄(abid 為空)拿到 GSS
      const prod = (/do_switchto\(\s*['"]([^'"]+)['"]/.exec(r.text) || [])[1] || "";
      raw.prodAbid = prod;
      check("A2 production 取到非空 abid", !!prod, prod || "(空 → 後面全會失敗)");
      if (!abid) abid = prod;
    } catch (e) { say("FAIL", "A 通訊錄探測", e.message); }
    if (!abid) { console.warn("沒有 abid，中止。"); return summarize(raw); }
    raw.abid = abid;

    const enc = encodeURIComponent;
    const listUrl = (dir, page) => `/cgi-bin/adb2main_mds?command=list&tofield=widget&workingabid=${enc(abid)}&workingdirid=${enc(dir)}&pageno=${page}`;
    const rowsOf = (doc) => [...doc.querySelectorAll('input[name="Entries"]')].map((el) => ({
      value: el.getAttribute("value") || "", nick: (el.getAttribute("nick") || "").trim(),
      email: (el.getAttribute("email") || "").trim().toLowerCase(), type: el.getAttribute("adbetype") || "",
    }));
    const fetchRows = async (dir, page) => rowsOf((await get(listUrl(dir, page))).doc);

    // production 的兩個核心讀法，獨立重寫一份
    const fetchNode = async (dir) => {
      const members = new Map(), subs = new Map();
      let bottomed = false, pages = 0;
      for (let p = 1; p <= MAX_PAGES; p++) {
        const rows = await fetchRows(dir, p);
        rows.forEach((r) => {
          if (r.type === "D") { if (r.value) subs.set(r.value, r.nick || r.value); }
          else if (r.email) members.set(r.email, r.nick);
        });
        pages = p;
        if (rows.length < PAGE_SIZE) { bottomed = true; break; }
      }
      return { members, subs, pages, bottomed };
    };
    const fetchSubs = async (dir) => {
      const subs = new Map();
      for (let p = 1; p <= MAX_PAGES; p++) {
        const rows = await fetchRows(dir, p);
        let sawNonDir = false;
        rows.forEach((r) => {
          if (r.type === "D") { if (r.value) subs.set(r.value, r.nick || r.value); } else sawNonDir = true;
        });
        if (sawNonDir || rows.length < PAGE_SIZE) break;
      }
      return subs;
    };

    /* ---- B. 樹端點：拿頂層目錄當遞迴起點，並確認它仍給不出部門 ---- */
    // production 曾經靠 adb2tree_mds 的 do_opendir 找部門，實測那支只回三個固定節點。
    // 這項在 Mail2000 改版把部門放回樹裡時會 FAIL —— 那時 production 可以改用更省的做法。
    try {
      const r = await get(`/cgi-bin/adb2tree_mds?workingabid=${enc(abid)}&command=expand&open_dirid=&tofield=widget`);
      raw.fixtures["tree_expand"] = r.text;
      for (const m of r.text.matchAll(/do_opendir\(\s*['"]([^'"]+)['"]/g)) if (m[1] !== "/") roots.push(m[1]);
      check("B1 樹端點給得出頂層目錄（遞迴起點）", roots.length > 0, `${roots.length} 個`);
      const hasDept = dept && new RegExp(dept, "i").test(r.text);
      say(!hasDept ? "PASS" : "FAIL", "B2 樹端點仍不含部門（現況假設）",
        hasDept ? `竟然找到 ${dept}！樹端點可用了，production 可改用更省的做法` : "只有頂層目錄，部門要靠列表端點");
      raw.roots = roots;
    } catch (e) { say("FAIL", "B1 樹端點探測", e.message); }

    /* ---- C. Entries 解析與 adbetype 值域 ---- */
    let topDepts = new Map();
    try {
      calls = 0;
      const rows1 = await fetchRows(roots[0], 1);
      raw.rowsSample = rows1.slice(0, 4);
      const types = {};
      rows1.forEach((r) => { types[r.type] = (types[r.type] || 0) + 1; });
      check("C1 Entries 解析出資料", rows1.length > 0, `${rows1.length} 列，adbetype 分佈 ${JSON.stringify(types)}`);
      check("C2 只有 D / C 兩種 adbetype", Object.keys(types).every((t) => t === "D" || t === "C"),
        `實際：${Object.keys(types).join(", ")}` + (Object.keys(types).some((t) => t !== "D" && t !== "C") ? " → 有沒見過的型別，分類邏輯要補" : ""));
      const dirs = rows1.filter((r) => r.type === "D"), people = rows1.filter((r) => r.type === "C");
      check("C3 目錄項有路徑、人員項有 email",
        dirs.every((r) => r.value.startsWith("/")) && people.every((r) => EMAIL_RE.test(r.email)),
        `目錄樣本 ${dirs[0] ? dirs[0].value : "-"} / 人員樣本 ${people[0] ? people[0].email : "-"}`);
      // production 靠這個規律省掉大半請求：目錄排在成員前面
      const lastDir = rows1.map((r) => r.type).lastIndexOf("D");
      const firstC = rows1.map((r) => r.type).indexOf("C");
      say(firstC < 0 || lastDir < firstC ? "PASS" : "FAIL", "C4 目錄項排在成員項前面（fetchSubs 提前停的前提）",
        firstC < 0 ? "本頁全是目錄" : `最後一個 D 在 #${lastDir}，第一個 C 在 #${firstC}`);

      calls = 0;
      for (const r of roots) (await fetchSubs(r)).forEach((n, p) => topDepts.set(p, n));
      check("C5 fetchSubs 每個根只讀 1 頁", calls === roots.length,
        `${calls} 支請求 / ${topDepts.size} 個部門（成員多時全讀要好幾十頁）`);
      raw.topDepts = [...topDepts.keys()];
    } catch (e) { say("FAIL", "C Entries 解析", e.message); }

    /* ---- D. 分頁行為 ---- */
    if (!dept) {
      say("SKIP", "D–G 需要指定部門",
        `可選頂層部門：${[...topDepts.values()].join(", ") || "(讀不到)"} → 再跑 adb2probe("代碼")`);
      return summarize(raw);
    }
    const target = [...topDepts.keys()].find((p) => p.toUpperCase().includes(dept.toUpperCase()));
    if (!target) {
      say("FAIL", "D0 找到目標部門", `${dept} 不在頂層部門裡：${[...topDepts.values()].join(", ")}`);
      return summarize(raw);
    }
    raw.target = target;
    try {
      const node = await fetchNode(target);
      check("D1 目標部門讀到底（未撞分頁上限）", node.bottomed,
        `${target}：${node.pages} 頁 / 本層 ${node.members.size} 人 / ${node.subs.size} 個子部門`);
      // production 的分頁停止條件靠「未滿 25 即最後一頁」；順手確認超界頁不會餵回新資料
      const over = rowsOf((await get(listUrl(target, node.pages + 5))).doc);
      const overEmails = over.filter((r) => r.email).map((r) => r.email);
      const known = new Set([...node.members.keys()]);
      say(overEmails.every((e) => known.has(e)) ? "PASS" : "FAIL", "D2 超界頁不會餵回沒見過的人",
        `第 ${node.pages + 5} 頁回 ${over.length} 列，` +
        (overEmails.every((e) => known.has(e)) ? "全是已知（回捲到第 1 頁）" : `有 ${overEmails.filter((e) => !known.has(e)).length} 筆新的 → 停止條件會漏人`));
      raw.targetNode = { members: node.members.size, subs: [...node.subs.keys()], pages: node.pages };
    } catch (e) { say("FAIL", "D 分頁探測", e.message); }

    /* ---- E. 遞迴（R1 的真正驗證）---- */
    try {
      calls = 0;
      const members = new Map(), visited = new Set(), queue = [target];
      let maxDepth = 0;
      while (queue.length && visited.size < MAX_NODES) {
        const dir = queue.shift();
        if (visited.has(dir)) continue;
        visited.add(dir);
        maxDepth = Math.max(maxDepth, (dir.match(/\//g) || []).length);
        const n = await fetchNode(dir);
        n.members.forEach((nick, em) => { if (!members.has(em)) members.set(em, nick); });
        n.subs.forEach((_, d) => { if (!visited.has(d)) queue.push(d); });
      }
      check("E1 遞迴有終止", queue.length === 0, `${visited.size} 個部門 / ${calls} 支請求 / 最深 ${maxDepth} 層`);
      const own = raw.targetNode ? raw.targetNode.members : 0;
      check("E2 遞迴真的多抓到人（否則等於寫死兩層）", members.size > own,
        `本層 ${own} 人 → 遞迴 ${members.size} 人（多 ${members.size - own}）`);
      check("E3 請求數 ≈ 部門數（未滿頁提前停有效）", calls <= visited.size * 2,
        `${calls} 支 / ${visited.size} 節點`);
      raw.recursive = { nodes: visited.size, members: members.size, requests: calls, maxDepth };
      raw.members = [...members].map(([e, n]) => `${n} <${e}>`);
      // 全是小寫才能用 existing().has(email.toLowerCase()) 判定
      check("E4 email 全為小寫", [...members.keys()].every((e) => e === e.toLowerCase()), "");
    } catch (e) { say("FAIL", "E 遞迴探測", e.message); }

    /* ---- F. chip 的 data-id（addOne 判定的前提）---- */
    // addOne 的前提：欄位必須可見，隱藏時 widget 不收輸入、chip 永遠不出現
    const inp = document.querySelector(".scheduleAttendeeInput");
    say(inp && inp.offsetParent ? "PASS" : "FAIL", "F0 與會者欄位可用",
      !inp ? "找不到欄位（不在行事曆頁？）"
        : inp.offsetParent ? "可見" : "找到但隱藏 → 請開啟事件編輯畫面並切到「與會者」頁籤，否則加人一定失敗");
    const chips = [...document.querySelectorAll(`${SEL.list} ${SEL.item}[data-id]`)];
    if (!chips.length) {
      say("SKIP", "F1 chip 的 data-id 格式",
        "畫面上沒有與會者。請在「與會者」頁籤手動加一個人再跑（探針不自己加，保持唯讀）");
    } else {
      const ids = chips.map((c) => c.getAttribute("data-id") || "");
      raw.fixtures["chip_outerHTML"] = chips[0].outerHTML;
      raw.chipIds = ids;
      check("F1 data-id 就是 email", ids.every((x) => EMAIL_RE.test(x)), `樣本：${ids.slice(0, 3).join(", ")}`);
      check("F2 data-id 已是小寫（existing().has(key) 才成立）", ids.every((x) => x === x.toLowerCase()), "");
    }

    /* ---- G. 全樹（選配，約 20 秒）---- */
    if (opts.full) {
      try {
        calls = 0;
        const t0 = performance.now();
        const all = new Map(topDepts);
        const queue = [...all.keys()];
        while (queue.length && all.size < MAX_NODES) {
          const dir = queue.shift();
          (await fetchSubs(dir)).forEach((n, p) => { if (!all.has(p)) { all.set(p, n); queue.push(p); } });
        }
        const depths = [...all.keys()].map((p) => (p.match(/\//g) || []).length);
        check("G1 全樹搜尋有終止（未撞節點上限）", queue.length === 0,
          `${all.size} 個部門 / ${calls} 支請求 / ${Math.round(performance.now() - t0)}ms / 最深 ${Math.max(...depths)} 層`);
        check("G2 節點上限還夠用", all.size < MAX_NODES * 0.8,
          `${all.size} / 上限 ${MAX_NODES}` + (all.size >= MAX_NODES * 0.8 ? " → 快撞上了，該調高 MAX_NODES" : ""));
        raw.fullTree = [...all.keys()];
      } catch (e) { say("FAIL", "G 全樹探測", e.message); }
    } else {
      say("SKIP", "G 全樹探測", "要跑請加 { full: true }（部門多時要跑上幾十秒）");
    }

    return summarize(raw);
  }

  function summarize(raw) {
    const n = (k) => results.filter((r) => r.kind === k).length;
    console.log(`%c--- ${n("PASS")} PASS / ${n("FAIL")} FAIL / ${n("SKIP")} SKIP ---`,
      "font-weight:700;" + (n("FAIL") ? "color:#dc2626" : "color:#16a34a"));
    const fails = results.filter((r) => r.kind === "FAIL");
    if (fails.length) console.log("需要處理：\n" + fails.map((r) => "  ✗ " + r.name + " — " + r.msg).join("\n"));
    raw.results = results.map((r) => ({ ...r }));
    window.__adb2probe = raw;
    console.log("%c原始回應/解析結果存在 window.__adb2probe", "color:#64748b");
    return raw;
  }

  window.adb2probe = adb2probe;
  console.log('%cadb2 迴歸測試已載入。跑：await adb2probe("<部門代碼>")，或 await adb2probe() 先列出部門',
    "color:#2563eb;font-weight:600");
})();
