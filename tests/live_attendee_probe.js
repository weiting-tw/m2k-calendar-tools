/* 實機測試 — 量「加入與會者」的真實延遲，驗證 addOne 的輪詢判定。
 *
 * 為什麼需要手動跑：與會者欄位只在 Mail2000 自己開啟事件編輯畫面、且切到
 * 「與會者」頁籤時才會啟用。從 console 強制顯示 DOM 或合成滑鼠事件都無法
 * 重現那個狀態（試過：fullcalendar 的 select handler 會被呼叫但不開表單；
 * 移除隱藏 class 後欄位仍 offsetParent === null）。所以這一步需要真人操作。
 *
 * 用法：
 *   1. Mail2000 行事曆 → 開啟「新增事件 / 會議排程」的編輯畫面
 *   2. 切到「與會者」頁籤（欄位要看得到）
 *   3. DevTools Console 貼上本檔全文
 *   4. await attendeeProbe(["某人@公司網域", "另一人@公司網域"])
 *      不給參數則只做環境檢查，不加任何人
 *
 * 安全性：只填欄位、按 Enter，**絕不按儲存** —— 所以不會建立會議、不會寄通知。
 * 測完會自動移除本次加入的 chip（純前端移除，未儲存所以伺服器端本來就沒有紀錄）。
 * 已存在的與會者不會被動到。
 */
(function () {
  "use strict";
  const SEL = { input: ".scheduleAttendeeInput", list: ".scheduleAttendeeList", item: ".scheduleAttendee" };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // 與 production 相同的參數，才量得出它夠不夠用
  const ADD_TIMEOUT_MS = 2000, ADD_POLL_MS = 50;

  const say = (kind, name, msg) => {
    const css = kind === "PASS" ? "color:#16a34a;font-weight:600"
      : kind === "FAIL" ? "color:#dc2626;font-weight:600" : "color:#94a3b8;font-weight:600";
    console.log("%c" + kind + "%c " + name + (msg ? " — " + msg : ""), css, "");
  };
  const existing = () => {
    const s = new Set();
    document.querySelectorAll(`${SEL.list} ${SEL.item}[data-id]`)
      .forEach((x) => s.add((x.getAttribute("data-id") || "").toLowerCase()));
    return s;
  };

  async function addOne(inp, email) {
    const key = email.toLowerCase();
    const t0 = performance.now();
    inp.focus(); inp.value = email; inp.dispatchEvent(new Event("input", { bubbles: true }));
    for (const t of ["keydown", "keypress", "keyup"]) {
      inp.dispatchEvent(new KeyboardEvent(t, { bubbles: true, key: "Enter", keyCode: 13, which: 13 }));
    }
    for (let waited = 0; waited < ADD_TIMEOUT_MS; waited += ADD_POLL_MS) {
      await sleep(ADD_POLL_MS);
      if (existing().has(key)) { inp.value = ""; return { email, ok: true, ms: Math.round(performance.now() - t0) }; }
    }
    inp.value = "";
    return { email, ok: false, ms: Math.round(performance.now() - t0) };
  }

  async function attendeeProbe(emails) {
    console.log("%c=== 與會者加入探測 ===", "font-weight:700;font-size:13px");
    const raw = { at: new Date().toISOString(), trials: [] };

    /* A. 環境：欄位必須真的可用 */
    const inp = document.querySelector(SEL.input);
    if (!inp) { say("FAIL", "A1 找到與會者欄位", "不在行事曆頁？"); return raw; }
    const visible = !!inp.offsetParent;
    say(visible ? "PASS" : "FAIL", "A1 與會者欄位可見",
      visible ? "" : "欄位藏著 → 請先開啟事件編輯畫面並切到「與會者」頁籤（否則 widget 不收輸入，每人會白等滿 2 秒）");
    if (!visible) return raw;
    const bound = !!(window.jQuery && (window.jQuery(inp).data("uiAutocomplete") || window.jQuery(inp).data("autocomplete")));
    say(bound ? "PASS" : "SKIP", "A2 autocomplete widget 已綁定", bound ? "" : "抓不到（不一定是問題，看 B 組結果）");

    const preChips = [...document.querySelectorAll(`${SEL.list} ${SEL.item}[data-id]`)];
    const preIds = new Set(preChips.map((c) => (c.getAttribute("data-id") || "").toLowerCase()));
    raw.preExisting = preIds.size;
    say("PASS", "A3 現有與會者", `${preIds.size} 位（本次不會動到他們）`);

    /* B. chip 的 data-id 格式 —— addOne 靠它判定成功 */
    if (preChips.length) {
      const ids = [...preIds];
      const allEmail = ids.every((x) => /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(x));
      const allLower = preChips.every((c) => {
        const v = c.getAttribute("data-id") || "";
        return v === v.toLowerCase();
      });
      say(allEmail ? "PASS" : "FAIL", "B1 data-id 就是 email", `樣本 ${ids[0]}`);
      say(allLower ? "PASS" : "FAIL", "B2 data-id 已是小寫",
        allLower ? "existing().has(email.toLowerCase()) 判定成立" : "有大寫 → 判定仍成立（existing 會轉小寫），但別改成直接比 data-id");
      raw.chipSample = preChips[0].outerHTML.slice(0, 300);
    } else {
      say("SKIP", "B chip 格式", "畫面上還沒有與會者；下面加入後會再驗一次");
    }

    if (!emails || !emails.length) {
      say("SKIP", "C 加入延遲", '沒給 email。要量延遲請跑 attendeeProbe(["某人@公司網域"])');
      window.__attendeeProbe = raw;
      return raw;
    }

    /* C. 真實延遲 —— 這是 ADD_TIMEOUT_MS 該設多少的依據 */
    const added = [];
    for (const em of emails) {
      if (preIds.has(em.toLowerCase())) { say("SKIP", `C ${em}`, "本來就在名單裡，跳過"); continue; }
      const t = await addOne(inp, em);
      raw.trials.push(t);
      if (t.ok) added.push(em.toLowerCase());
      say(t.ok ? "PASS" : "FAIL", `C ${em}`, `${t.ms}ms${t.ok ? "" : "（等滿 timeout 仍沒出現）"}`);
    }
    const oks = raw.trials.filter((t) => t.ok);
    if (oks.length) {
      const ms = oks.map((t) => t.ms);
      const max = Math.max(...ms), avg = Math.round(ms.reduce((a, b) => a + b, 0) / ms.length);
      say("PASS", "C1 成功路徑延遲", `平均 ${avg}ms、最慢 ${max}ms`);
      say(max < ADD_TIMEOUT_MS * 0.5 ? "PASS" : "FAIL", "C2 timeout 餘裕充足",
        `最慢 ${max}ms vs 上限 ${ADD_TIMEOUT_MS}ms` +
        (max >= ADD_TIMEOUT_MS * 0.5 ? " → 餘裕不足兩倍，建議調高 ADD_TIMEOUT_MS" : ""));
      say("PASS", "C3 與舊版固定等待相比",
        `舊版每人固定 380ms；實測平均 ${avg}ms → ${avg < 380 ? "輪詢更快" : "舊版的 380ms 其實不夠，會誤判成失敗"}`);
      // 加入後再驗一次 data-id（這次是我們自己送進去的值）
      const now = existing();
      const matched = added.every((e) => now.has(e));
      say(matched ? "PASS" : "FAIL", "C4 送進去的 email 與 data-id 一致",
        matched ? "" : "data-id 被伺服器改寫過（大小寫或加了顯示名）→ 判定方式要調整");
    }

    /* D. 清理：只移除本次加入的，不動原有的 */
    let removed = 0;
    document.querySelectorAll(`${SEL.list} ${SEL.item}[data-id]`).forEach((c) => {
      const v = (c.getAttribute("data-id") || "").toLowerCase();
      if (added.includes(v) && !preIds.has(v)) { c.remove(); removed++; }
    });
    inp.value = "";
    say("PASS", "D1 已清理本次加入的與會者", `移除 ${removed} 個（未按儲存，伺服器端本來就沒紀錄）`);
    say(document.querySelectorAll(`${SEL.list} ${SEL.item}[data-id]`).length === preIds.size ? "PASS" : "FAIL",
      "D2 回到原本的與會者數", `${document.querySelectorAll(`${SEL.list} ${SEL.item}[data-id]`).length} vs 原本 ${preIds.size}`);

    console.log("%c提醒：請不要按儲存，直接關掉編輯畫面即可。", "color:#dc2626;font-weight:600");
    window.__attendeeProbe = raw;
    console.log("%c結果存在 window.__attendeeProbe", "color:#64748b");
    return raw;
  }

  window.attendeeProbe = attendeeProbe;
  console.log('%c與會者探測已載入。先開好事件編輯畫面的「與會者」頁籤，然後：\n' +
    '  await attendeeProbe()                      // 只檢查環境\n' +
    '  await attendeeProbe(["某人@公司網域"])      // 量真實延遲',
    "color:#2563eb;font-weight:600");
})();
