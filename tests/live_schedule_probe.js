/* 實機探測 — 驗證 m2k-calendar.user.js 對 Mail2000 排程端點的行為假設。
 *
 * 為什麼是這種形式：「查看與會者空檔」建立在幾個只有伺服器能回答的假設上：
 * 排程端點對「沒分享給我」的同事也回事件、dtstart 是純 epoch 秒不必加 offset、
 * attendee_reply_status 的數字對應、組織信箱查起來長什麼樣。這些在 node 裡測不到
 * （沒 session），所以問伺服器本人，做成可重複跑。
 *
 * 用法：
 *   1. 在已登入的 Mail2000 行事曆頁開 DevTools Console，貼上本檔全文
 *   2. await schedprobe("colleague@example.com")                   // 一位「沒分享給你」的同事
 *      await schedprobe("colleague@example.com", { org: "group_box@example.com" })  // 順便查組織信箱
 *      await schedprobe("colleague@example.com", { self: "me@example.com" })        // 自動抓不到自己 email 時手動給
 *   3. 每項印 PASS / FAIL / SKIP / INFO；結束後 window.__schedprobe 有原始回應可複製
 *
 * 唯讀：只發 GET，不改任何狀態。
 * 注意：輸出含真實行程內容（標題、與會者），貼給他人前請自行斟酌。
 */
(function () {
  "use strict";
  const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/;
  const SCHED = "/cgi-bin/cal/calsrv/schedule/", FEEDS = "/cgi-bin/cal/calsrv/feeds/default/";
  const results = [];
  const say = (kind, name, msg) => {
    results.push({ kind, name, msg });
    const css = kind === "PASS" ? "color:#16a34a;font-weight:600" : kind === "FAIL" ? "color:#dc2626;font-weight:600"
      : kind === "INFO" ? "color:#2563eb;font-weight:600" : "color:#94a3b8;font-weight:600";
    console.log("%c" + kind + "%c " + name + (msg ? " — " + msg : ""), css, "");
  };
  const check = (name, cond, msg) => say(cond ? "PASS" : "FAIL", name, msg);
  const get = async (url) => {
    const r = await fetch(url, { credentials: "include" });
    const text = await r.text();
    let json = null; try { json = JSON.parse(text); } catch (_) {}
    return { status: r.status, text, json };
  };
  const strip = (s) => String(s || "").replace(/^mailto:/i, "").toLowerCase();
  const local = (epoch) => new Date(epoch * 1000).toLocaleString("zh-TW", { hour12: false });
  const REPLY = { 0: "未回覆", 1: "已接受", 2: "已拒絕", 3: "暫定" };

  async function schedprobe(other, opts = {}) {
    results.length = 0;
    const raw = { at: new Date().toISOString(), other, opts, fixtures: {} };
    window.__schedprobe = raw;
    console.log(`%c=== 排程端點探測：${other} ===`, "font-weight:700;font-size:13px");
    const now = Math.floor(Date.now() / 1000);
    const st = now - 3 * 86400, et = now + 11 * 86400;   // 前 3 天到後 11 天，含跨週
    const url = (email) => SCHED + encodeURIComponent(email) + "/instances?starttime=" + st + "&endtime=" + et;

    /* ---- A. 沒分享給我的同事 ---- */
    const a = await get(url(other));
    raw.fixtures.other = a.json || a.text.slice(0, 500);
    check("A1 HTTP 200", a.status === 200, `HTTP ${a.status}`);
    check("A2 回 JSON 且 rspCode 0", a.json && Number(a.json.rspCode) === 0, a.json ? `rspCode=${a.json.rspCode} ${a.json.rspMsg || ""}` : "不是 JSON");
    const inst = (a.json && a.json.instances) || [];
    check("A3 instances 是陣列且有內容（對方兩週內至少有一場行程；若真的沒有，這項 FAIL 可忽略）", Array.isArray(inst) && inst.length > 0, `${inst.length} 筆`);
    if (inst.length) {
      const sample = inst[0];
      check("A4 有 summary / dtstart / dtend 欄位", "summary" in sample && "dtstart" in sample && "dtend" in sample, Object.keys(sample).join(","));
      // dtstart 直接當 epoch 秒：多數行程應落在本地 06:00–23:00；若大半落在深夜，代表其實要加 offset
      const hours = inst.map((i) => new Date(Number(i.dtstart) * 1000).getHours());
      const sane = hours.filter((h) => h >= 6 && h <= 23).length;
      check("A5 dtstart 直接是 epoch 秒（多數事件落在 06–23 時）", sane >= hours.length * 0.7, `${sane}/${hours.length} 落在白天；樣本：${inst.slice(0, 3).map((i) => `${local(i.dtstart)} ${i.summary}`).join(" | ")}`);
      const offsets = [...new Set(inst.map((i) => JSON.stringify(i.offset)))];
      say("INFO", "A6 offset 欄位出現的值與型別", offsets.join(", "));
      // 回覆狀態分布：讓你對照 webmail 畫面，確認 2=已拒絕、3=暫定
      const dist = {};
      inst.forEach((i) => (Array.isArray(i.attendee) ? i.attendee : []).forEach((x) => {
        if (strip(x.attendee) === other.toLowerCase()) dist[x.attendee_reply_status] = (dist[x.attendee_reply_status] || 0) + 1;
      }));
      say("INFO", "A7 該人在自己行程裡的 attendee_reply_status 分布（請對照 webmail 確認 2=已拒絕、3=暫定）",
        Object.entries(dist).map(([k, v]) => `${k}(${REPLY[k] || "?"})×${v}`).join(", ") || "沒有任何一筆列他為與會者");
      const nonZero = inst.filter((i) => (Array.isArray(i.attendee) ? i.attendee : []).some((x) => strip(x.attendee) === other.toLowerCase() && [2, 3].includes(Number(x.attendee_reply_status))));
      if (nonZero.length) say("INFO", "A8 含 2/3 狀態的事件（到 webmail 開這幾筆核對）", nonZero.slice(0, 5).map((i) => `#${i.id} ${i.summary} @ ${local(i.dtstart)}`).join(" | "));
      else say("SKIP", "A8 這兩週沒有 2/3 狀態的事件可核對");
      const multi = inst.filter((i) => Number(i.dtend) - Number(i.dtstart) >= 86400);
      say("INFO", "A9 跨 24 小時以上的事件（腳本會標「請假/不在」）", multi.length ? multi.map((i) => `${i.summary} ${local(i.dtstart)}→${local(i.dtend)}`).join(" | ") : "無");
      // 查詢窗從事件「開始之後」起算，它還會不會被回傳？2026-09-21 實測 PASS（區間重疊比對），
      // 腳本因此不前推查詢窗。若哪天 FAIL，代表伺服器改成只比對 dtstart，上週開始的請假會漏，得補救
      const long = inst.find((i) => Number(i.dtend) - Number(i.dtstart) >= 7200);
      if (long) {
        const mid = Number(long.dtstart) + 3600;
        const w = await get(SCHED + encodeURIComponent(other) + "/instances?starttime=" + mid + "&endtime=" + Number(long.dtend));
        const hit = w.json && Array.isArray(w.json.instances) && w.json.instances.some((i) => String(i.id) === String(long.id));
        check("A10 查詢窗起點落在事件中間時，該事件仍被回傳（跨日請假不會漏）", !!hit, `#${long.id} ${long.summary}，窗 ${local(mid)} 起`);
      } else say("SKIP", "A10 沒有長度 ≥ 2 小時的事件可測窗口重疊");
    }

    /* ---- B. 查自己：找自己的 email + 與 feeds 端點比對時間戳 ---- */
    let me = opts.self || "";
    const fl = await get(FEEDS + "default/");
    raw.fixtures.feedsList = fl.json || fl.text.slice(0, 500);
    if (!me) {
      const m = JSON.stringify(fl.json || {}).match(EMAIL_RE);
      me = m ? m[0].toLowerCase() : "";
    }
    say(me ? "INFO" : "SKIP", "B1 自己的 email（feeds 清單實測沒有 email，腳本改為直接問使用者；要跑 B 組請帶 { self: \"me@…\" }）", me || `feeds default 清單裡沒有 email，欄位：${fl.json && fl.json.calendars && fl.json.calendars[0] ? Object.keys(fl.json.calendars[0]).join(",") : "?"}`);
    if (me) {
      const b = await get(url(me));
      raw.fixtures.self = b.json || b.text.slice(0, 500);
      check("B2 排程端點查自己也 200 / rspCode 0", b.status === 200 && b.json && Number(b.json.rspCode) === 0, `HTTP ${b.status}`);
      const cal = fl.json && fl.json.calendars && fl.json.calendars[0];
      if (cal && b.json) {
        const f = await get(FEEDS + (cal.feeds || "default") + "/" + cal.id + "/events/instances/?starttime=" + st + "&endtime=" + et);
        raw.fixtures.feedsEvents = f.json || f.text.slice(0, 500);
        const fi = (f.json && f.json.instances) || [], si = b.json.instances || [];
        const byId = new Map(si.map((i) => [String(i.id), i]));
        const pairs = fi.filter((i) => byId.has(String(i.id)));
        if (pairs.length) {
          const same = pairs.filter((i) => Number(i.dtstart) === Number(byId.get(String(i.id)).dtstart)).length;
          check("B3 同一事件在 feeds 與排程端點的 dtstart 一致（兩邊都不必加 offset）", same === pairs.length, `${same}/${pairs.length} 一致`);
          const fo = [...new Set(fi.map((i) => JSON.stringify(i.offset)))];
          say("INFO", "B4 feeds 端點的 offset 值與型別", fo.join(", "));
        } else say("SKIP", "B3 feeds 與排程端點沒有同 id 的事件可比對");
      } else say("SKIP", "B3 沒有 default 行事曆可比對");
    }

    /* ---- C. 組織信箱（可選）---- */
    if (opts.org) {
      const c = await get(url(opts.org));
      raw.fixtures.org = c.json || c.text.slice(0, 500);
      say("INFO", "C1 組織信箱查排程端點", `HTTP ${c.status}，rspCode=${c.json && c.json.rspCode}，${c.json && c.json.instances ? c.json.instances.length + " 筆" : "無 instances"}`);
      if (c.json && Array.isArray(c.json.instances) && c.json.instances.length) {
        say("INFO", "C2 組織信箱的行程樣本（判斷它是否等於「成員行程的聯集」，還是只有它自己主辦的）",
          c.json.instances.slice(0, 5).map((i) => `${i.summary} @ ${local(i.dtstart)} organizer=${strip(i.organizer)}`).join(" | "));
      }
    } else say("SKIP", "C 未指定 org，略過組織信箱");

    /* ---- D. 不存在的帳號 ---- */
    // 網域取自受測位址：寫死公司網域既不該進版控，換環境也會測不出「查無此帳號」
    const ghostDomain = (other.split("@")[1] || "example.com");
    const d = await get(url("no-such-user-zz@" + ghostDomain));
    raw.fixtures.ghost = d.json || d.text.slice(0, 300);
    say("INFO", "D1 不存在的帳號回什麼（腳本會把非 0 的 rspCode 顯示在該列）", `HTTP ${d.status}，rspCode=${d.json && d.json.rspCode}，rspMsg=${d.json && d.json.rspMsg}，instances=${d.json && d.json.instances ? d.json.instances.length : "-"}`);

    const n = (k) => results.filter((r) => r.kind === k).length;
    console.log(`%c=== 完成：PASS ${n("PASS")} / FAIL ${n("FAIL")} / SKIP ${n("SKIP")} / INFO ${n("INFO")}；原始回應在 window.__schedprobe ===`, "font-weight:700");
    return results;
  }
  window.schedprobe = schedprobe;
  console.log("已載入。執行：await schedprobe(\"colleague@example.com\", { org: \"group_box@example.com\" })");
})();
