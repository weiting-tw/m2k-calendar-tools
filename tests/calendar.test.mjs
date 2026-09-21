/* m2k-calendar.user.js 的離線測試 — 不連伺服器、不需登入。
 * 執行:  npm test        （或 node --test tests/）
 *
 * userscript 是單檔 IIFE（為了「貼上即安裝」），結尾有一段只在 node 生效的
 * module.exports 掛鉤，讓這裡拿到內部函式，不必為可測性拆成模組。
 *
 * 這裡測的是「拿到伺服器回應之後，我方邏輯怎麼處理」——尤其是實機難觸發的分支：
 * 分頁截斷、節點上限、環狀路徑、連續失敗中止、欄位隱藏。
 * 「伺服器實際會怎麼回應」則由 tests/live_adb2_probe.js 實機驗證，兩者互補。
 */
import { test, describe, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const SCRIPT = path.resolve(import.meta.dirname, "../userscripts/m2k-calendar.user.js");

const PAGE = 25;   // 伺服器每頁固定筆數（live 探針 C 組驗證過）
/* 測試資料全為虛構。production 的部門樹根是動態發現的，所以這裡用假的根路徑 ROOT。 */
const ROOT = "/org";
let gb, dom, realSetTimeout;

/** 依 do_switchto 慣例回通訊錄清單：第一個 abid 是空的（個人），第二個才是 GSS */
const treeHtml = () =>
  `<a onclick="do_switchto('', '0')">personal</a><a onclick="do_switchto('BOOK1', '2')">company</a>`;

/** 把 rows 轉成 adb2main_mds 的列表 HTML（目錄項排在成員項前面，與實機一致） */
const rowsHtml = (rows) =>
  rows.map((r) => `<input type="checkbox" name="Entries" value="${r.value}" nick="${r.nick}" email="${r.email}" adbetype="${r.type}">`).join("");

/**
 * 假伺服器。tree 形如 { "/org/x": { members: ["a@example.test"], subs: ["/org/x/y"] } }
 * 會自動套用「目錄在前、成員在後」與 25 筆分頁，並記錄每支請求供斷言。
 */
function fakeServer(tree) {
  const calls = [];
  global.fetch = async (url) => {
    calls.push(url);
    const u = new URL(url, "https://mail.gss.com.tw");
    if (u.pathname.includes("adb2tree_mds")) {
      // 樹端點只列頂層目錄，不含部門（與實機一致）
      return { text: async () => `<a onclick="do_opendir('/')">root</a><a onclick="do_opendir('${ROOT}')">org</a>` };
    }
    if (u.pathname.includes("adb2tree")) return { text: async () => treeHtml() };
    const dir = u.searchParams.get("workingdirid") || "";
    const page = Number(u.searchParams.get("pageno") || 1);
    const node = tree[dir] || { members: [], subs: [] };
    const rows = [
      ...(node.subs || []).map((s) => ({ type: "D", value: s, nick: s.split("/").pop(), email: "" })),
      ...(node.members || []).map((m) => ({ type: "C", value: m, nick: `Name ${m}`, email: m })),
    ];
    const slice = rows.slice((page - 1) * PAGE, page * PAGE);
    return { text: async () => rowsHtml(slice) };
  };
  return calls;
}

function setupDom() {
  dom = new JSDOM(`<body><div class="scheduleAttendeeList"></div><input class="scheduleAttendeeInput"></body>`,
    { url: "https://mail.gss.com.tw/cgi-bin/cal/cal_main" });
  for (const k of ["window", "document", "DOMParser", "Event", "KeyboardEvent", "Node"]) {
    global[k] = k === "window" ? dom.window : dom.window[k];
  }
}

/** jsdom 不做 layout，offsetParent 永遠是 null —— 手動決定「可見」與否 */
function setVisible(el, visible) {
  Object.defineProperty(el, "offsetParent", { get: () => (visible ? dom.window.document.body : null), configurable: true });
}

let confirmCalls;
beforeEach(() => {
  setupDom();
  confirmCalls = [];
  global.confirm = (msg) => { confirmCalls.push(msg); return true; };   // 預設同意
  realSetTimeout = global.setTimeout;
  delete require.cache[SCRIPT];
  gb = require(SCRIPT);
  gb._reset();
});
afterEach(() => { global.setTimeout = realSetTimeout; });

/** 讓 sleep 立刻回來，把 2 秒的輪詢壓成瞬間（測分支，不測時間） */
const speedUpTimers = () => { global.setTimeout = (fn) => realSetTimeout(fn, 0); };

describe("fetchRows — Entries 解析", () => {
  test("依 adbetype 分出目錄與人，並抽出 value/nick/email", async () => {
    fakeServer({ [ROOT]: { subs: [`${ROOT}/unit-a`], members: ["person1@example.test"] } });
    await gb.loadDepts();                     // 先讓 GSSABID 就位
    const rows = await gb.fetchRows(ROOT, 1);
    assert.equal(rows.length, 2);
    assert.deepEqual(rows[0], { value: `${ROOT}/unit-a`, nick: "unit-a", email: "", type: "D" });
    assert.equal(rows[1].type, "C");
    assert.equal(rows[1].email, "person1@example.test");
  });

  test("email 一律轉小寫（addOne 靠小寫比對 chip）", async () => {
    global.fetch = async () => ({ text: async () => rowsHtml([{ type: "C", value: "X", nick: "X", email: "Mixed_CASE@Example.TEST" }]) });
    const rows = await gb.fetchRows("/d", 1);
    assert.equal(rows[0].email, "mixed_case@example.test");
  });
});

describe("fetchNode — 分頁", () => {
  test("未滿一頁就停，不多打請求", async () => {
    const calls = fakeServer({ "/d": { members: ["a@example.test", "b@example.test"], subs: [] } });
    await gb.loadDepts();
    const before = calls.length;
    const { members } = await gb.fetchNode("/d");
    assert.equal(members.size, 2);
    assert.equal(calls.length - before, 1, "只該讀 1 頁");
    assert.deepEqual(gb.NOTES, [], "沒截斷就不該留警告");
  });

  test("滿頁會續讀下一頁", async () => {
    const many = Array.from({ length: PAGE + 3 }, (_, i) => `p${i}@g`);
    const calls = fakeServer({ "/d": { members: many, subs: [] } });
    await gb.loadDepts();
    const before = calls.length;
    const { members } = await gb.fetchNode("/d");
    assert.equal(members.size, PAGE + 3);
    assert.equal(calls.length - before, 2);
    assert.deepEqual(gb.NOTES, [], "讀到底了就不該留警告");
  });

  test("讀滿上限仍未見底 → 記一筆截斷警告，不靜默", async () => {
    // 每頁都回滿 25 筆不重複資料，永遠不見底
    let n = 0;
    global.fetch = async (url) => {
      if (String(url).includes("adb2tree")) return { text: async () => treeHtml() };
      const rows = Array.from({ length: PAGE }, () => { n++; return { type: "C", value: `u${n}@g`, nick: "N", email: `u${n}@g` }; });
      return { text: async () => rowsHtml(rows) };
    };
    const { members } = await gb.fetchNode("/huge");
    assert.equal(members.size, gb.MAX_PAGES * PAGE, "應該剛好停在上限");
    assert.equal(gb.NOTES.length, 1);
    assert.match(gb.NOTES[0], /未見底/);
  });
});

describe("fetchSubs — 只要子部門的快版", () => {
  test("看到第一個非目錄項就停（實機規律：目錄排在成員前）", async () => {
    const calls = fakeServer({ "/d": { subs: ["/d/a", "/d/b"], members: Array.from({ length: 100 }, (_, i) => `m${i}@g`) } });
    await gb.loadDepts();
    const before = calls.length;
    const subs = await gb.fetchSubs("/d");
    assert.deepEqual([...subs.keys()], ["/d/a", "/d/b"]);
    assert.equal(calls.length - before, 1, "不該為了成員多讀 4 頁");
  });

  test("整頁都是目錄時會續讀", async () => {
    const subs30 = Array.from({ length: 30 }, (_, i) => `/d/s${i}`);
    const calls = fakeServer({ "/d": { subs: subs30, members: ["z@example.test"] } });
    await gb.loadDepts();
    const before = calls.length;
    const subs = await gb.fetchSubs("/d");
    assert.equal(subs.size, 30);
    assert.equal(calls.length - before, 2);
  });
});

describe("collectSubtree — 遞迴", () => {
  const tree = {
    "/r": { subs: ["/r/a", "/r/b"], members: ["r1@example.test"] },
    "/r/a": { subs: ["/r/a/x"], members: ["a1@example.test", "shared@example.test"] },
    "/r/b": { subs: [], members: ["b1@example.test"] },
    "/r/a/x": { subs: [], members: ["x1@example.test", "shared@example.test"] },
  };

  test("走遍所有子孫並跨部門去重", async () => {
    fakeServer(tree);
    await gb.loadDepts();
    const { emails, nodeCount } = await gb.collectSubtree("/r");
    assert.equal(nodeCount, 4);
    assert.deepEqual([...emails].sort(), ["a1@example.test", "b1@example.test", "r1@example.test", "shared@example.test", "x1@example.test"]);
  });

  test("遞迴真的比只看本層多抓到人（否則等於寫死兩層）", async () => {
    fakeServer(tree);
    await gb.loadDepts();
    const own = (await gb.fetchNode("/r")).members.size;
    const { emails } = await gb.collectSubtree("/r");
    assert.equal(own, 1);
    assert.ok(emails.length > own, `本層 ${own} 人，遞迴 ${emails.length} 人`);
  });

  test("子部門指回祖先也不會無限繞", async () => {
    fakeServer({
      "/r": { subs: ["/r/a"], members: ["r1@example.test"] },
      "/r/a": { subs: ["/r"], members: ["a1@example.test"] },   // 指回根
    });
    await gb.loadDepts();
    const { emails, nodeCount } = await gb.collectSubtree("/r");
    assert.equal(nodeCount, 2);
    assert.deepEqual([...emails].sort(), ["a1@example.test", "r1@example.test"]);
  });

  test("撞到節點上限 → 記警告，不假裝抓完了", async () => {
    // 每個節點都生一個新子節點，永遠展不完
    global.fetch = async (url) => {
      const s = String(url);
      if (s.includes("adb2tree")) return { text: async () => treeHtml() };
      const dir = new URL(s, "https://x").searchParams.get("workingdirid");
      return { text: async () => rowsHtml([{ type: "D", value: dir + "/n", nick: "n", email: "" }]) };
    };
    const { nodeCount } = await gb.collectSubtree("/r");
    assert.equal(nodeCount, gb.MAX_NODES);
    assert.ok(gb.NOTES.some((n) => /上限/.test(n)), `NOTES=${JSON.stringify(gb.NOTES)}`);
  });
});

describe("loadDepts / loadDeptsAll — 快取與全樹", () => {
  test("部門樹的根是從樹端點動態取得，不寫死路徑", async () => {
    fakeServer({ [ROOT]: { subs: [`${ROOT}/unit-a`], members: [] } });
    const roots = await gb.rootDirs();
    assert.deepEqual(roots, [ROOT], "應排除 '/' 只留真正的頂層目錄");
  });

  test("樹端點什麼都沒回 → 留下警告而非默默查不到", async () => {
    global.fetch = async (url) => String(url).includes("adb2tree_mds")
      ? { text: async () => `<a onclick="do_opendir('/')">root</a>` }
      : { text: async () => treeHtml() };
    const roots = await gb.rootDirs();
    assert.deepEqual(roots, []);
    assert.ok(gb.NOTES.some((n) => /頂層目錄/.test(n)), `NOTES=${JSON.stringify(gb.NOTES)}`);
  });

  test("第二次呼叫不再發請求", async () => {
    const calls = fakeServer({ [ROOT]: { subs: [`${ROOT}/unit-a`], members: [] } });
    await gb.loadDepts();
    const after = calls.length;
    await gb.loadDepts();
    assert.equal(calls.length, after);
  });

  test("全樹會遞迴到底（頂層搜不到的深層部門也找得到）", async () => {
    fakeServer({
      [ROOT]: { subs: [`${ROOT}/lv1`], members: [] },
      [`${ROOT}/lv1`]: { subs: [`${ROOT}/lv1/lv2`], members: [] },
      [`${ROOT}/lv1/lv2`]: { subs: [`${ROOT}/lv1/lv2/lv3`], members: [] },
      [`${ROOT}/lv1/lv2/lv3`]: { subs: [], members: ["deep@example.test"] },
    });
    const top = await gb.loadDepts();
    assert.equal(top.length, 1, "頂層只看得到第一層");
    const all = await gb.loadDeptsAll();
    assert.ok(all.some((d) => d.path.endsWith("lv3")), "全樹才找得到深層部門");
  });
});

describe("addOne / addMany — 加入與會者", () => {
  const list = () => dom.window.document.querySelector(".scheduleAttendeeList");
  const input = () => dom.window.document.querySelector(".scheduleAttendeeInput");
  const addChip = (email) => {
    const chip = dom.window.document.createElement("div");
    chip.className = "scheduleAttendee";
    chip.setAttribute("data-id", email.toLowerCase());
    list().appendChild(chip);
  };
  /** 模擬原生 widget：按下 Enter 後「過一會兒」才插 chip */
  const wireWidget = ({ accept = () => true } = {}) => {
    const inp = input();
    setVisible(inp, true);
    inp.addEventListener("keyup", () => {
      const v = inp.value;
      if (v && accept(v)) global.setTimeout(() => addChip(v), 1);
    });
    return inp;
  };

  test("chip 出現就回成功，不等滿 timeout", async () => {
    speedUpTimers(); wireWidget();
    const t0 = Date.now();
    assert.equal(await gb.addOne("a@example.test"), true);
    assert.ok(Date.now() - t0 < gb.ADD_TIMEOUT_MS, "不該等滿 2 秒");
    assert.ok(gb.existing().has("a@example.test"));
  });

  test("chip 一直沒出現才回失敗（且真的等了一輪）", async () => {
    wireWidget({ accept: () => false });   // 不加速：確認 timeout 真的是 2 秒級
    const t0 = Date.now();
    assert.equal(await gb.addOne("nobody@example.test"), false);
    const spent = Date.now() - t0;
    assert.ok(spent >= gb.ADD_TIMEOUT_MS * 0.8, `只等了 ${spent}ms`);
  });

  test("大小寫不同的 email 也認得出自己的 chip", async () => {
    speedUpTimers(); wireWidget();
    assert.equal(await gb.addOne("Flora_HU@GSS.com.tw"), true);
  });

  test("欄位藏著時直接說明原因，不逐一白等", async () => {
    setVisible(input(), false);
    const logs = [];
    const t0 = Date.now();
    assert.equal(await gb.addMany(["a@example.test", "b@example.test", "c@example.test"], (m) => logs.push(m)), 0);
    assert.ok(Date.now() - t0 < 500, "不該花上 3 輪 timeout");
    assert.match(logs.join("\n"), /頁籤|藏著/);
  });

  test("跳過已在名單的人，輸入重複也只加一次", async () => {
    speedUpTimers(); wireWidget();
    addChip("old@example.test");
    const logs = [];
    const ok = await gb.addMany(["old@example.test", "new@example.test", "NEW@example.test"], (m) => logs.push(m));
    assert.equal(ok, 1);
    assert.equal(gb.existing().size, 2);
  });

  test("全部都已在名單時不會誤報加入", async () => {
    speedUpTimers(); wireWidget();
    addChip("a@example.test");
    const logs = [];
    assert.equal(await gb.addMany(["a@example.test"], (m) => logs.push(m)), 0);
    assert.match(logs.join("\n"), /沒有新成員/);
  });

  test("連續失敗達上限就中止，且失敗名單含被跳過的人", async () => {
    speedUpTimers(); wireWidget({ accept: () => false });
    const logs = [];
    const todo = Array.from({ length: gb.MAX_FAIL_STREAK + 4 }, (_, i) => `f${i}@g`);
    const ok = await gb.addMany(todo, (m) => logs.push(m));
    assert.equal(ok, 0);
    const text = logs.join("\n");
    assert.match(text, /中止/);
    // 名單要完整：嘗試過的 + 跳過的 = 全部
    assert.match(text, new RegExp(`✗ ${todo.length} 位沒進去`));
  });

  test("失敗後又成功會重置連續計數（不會提早中止）", async () => {
    speedUpTimers();
    const inp = input(); setVisible(inp, true);
    let n = 0;
    inp.addEventListener("keyup", () => {
      const v = inp.value;
      n++;
      if (v && n % 2 === 0) global.setTimeout(() => addChip(v), 1);   // 一半成功
    });
    const logs = [];
    const todo = Array.from({ length: 8 }, (_, i) => `h${i}@g`);
    const ok = await gb.addMany(todo, (m) => logs.push(m));
    assert.ok(ok >= 3, `只成功 ${ok} 位`);
    assert.doesNotMatch(logs.join("\n"), /中止/);
  });
});

describe("addMany — 大批加入前的規模確認", () => {
  const list = () => dom.window.document.querySelector(".scheduleAttendeeList");
  const input = () => dom.window.document.querySelector(".scheduleAttendeeInput");
  const wire = () => {
    const inp = input();
    setVisible(inp, true);
    inp.addEventListener("keyup", () => {
      const v = inp.value;
      if (v) global.setTimeout(() => {
        const chip = dom.window.document.createElement("div");
        chip.className = "scheduleAttendee";
        chip.setAttribute("data-id", v.toLowerCase());
        list().appendChild(chip);
      }, 1);
    });
  };
  const many = (n) => Array.from({ length: n }, (_, i) => `p${i}@example.test`);

  test("人數在門檻內不打擾使用者", async () => {
    speedUpTimers(); wire();
    await gb.addMany(many(5), () => {});
    assert.equal(confirmCalls.length, 0);
  });

  test("超過門檻會先問，訊息要有人數與預估時間", async () => {
    speedUpTimers(); wire();
    await gb.addMany(many(gb.BIG_ADD + 1), () => {});
    assert.equal(confirmCalls.length, 1);
    assert.match(confirmCalls[0], new RegExp(String(gb.BIG_ADD + 1)));
    assert.match(confirmCalls[0], /分|秒/);
  });

  test("按取消就不加人，改把名單印出來給使用者複製", async () => {
    speedUpTimers(); wire();
    global.confirm = (msg) => { confirmCalls.push(msg); return false; };
    const logs = [];
    const todo = many(gb.BIG_ADD + 3);
    const ok = await gb.addMany(todo, (m) => logs.push(m));
    assert.equal(ok, 0);
    assert.equal(gb.existing().size, 0, "取消就不該加任何人");
    const text = logs.join("\n");
    assert.match(text, /只列名單/);
    assert.ok(text.includes(todo[0]) && text.includes(todo[todo.length - 1]),
      "名單要完整印出，否則使用者複製不到");
  });

  test("門檻是看去重後的實際待加人數", async () => {
    speedUpTimers(); wire();
    // 同一個人重複 300 次，實際只要加 1 位 → 不該問
    await gb.addMany(Array(gb.BIG_ADD + 100).fill("dup@example.test"), () => {});
    assert.equal(confirmCalls.length, 0);
    assert.equal(gb.existing().size, 1);
  });
});

describe("esc — 通訊錄字串進 innerHTML 前的跳脫", () => {
  test("跳脫五個危險字元", () => {
    assert.equal(gb.esc(`<script>"x"&'y'</script>`),
      "&lt;script&gt;&quot;x&quot;&amp;&#39;y&#39;&lt;/script&gt;");
  });
  test("null / undefined 不會炸", () => {
    assert.equal(gb.esc(null), "");
    assert.equal(gb.esc(undefined), "");
  });
});

describe("flushNotes — 警告要送得出去", () => {
  test("倒進 log 後清空，不會重複報同一則", () => {
    gb.NOTES.push("something truncated");
    const logs = [];
    gb.flushNotes((m) => logs.push(m));
    assert.equal(logs.length, 1);
    assert.match(logs[0], /something truncated/);
    gb.flushNotes((m) => logs.push(m));
    assert.equal(logs.length, 1, "第二次不該再報");
  });
});

/* ======================= 他人行事曆（排程端點）與時間軸 ======================= */
// 以下時間戳都用台北時間建，避免測試機時區不同造成漂移
const TZ_NOTE = "時間用本地 new Date(y,m,d,h) 建、用 getHours() 讀，任何時區都成立；package.json 釘 TZ 只是讓輸出好讀";
const at = (y, mo, d, h, mi = 0) => Math.floor(new Date(y, mo - 1, d, h, mi) / 1000);
const ME = "me@example.test", OTHER = "other@example.test";
/** 一筆排程端點的 instance；attendees 是 [email, reply_status] 陣列 */
const inst = (dtstart, dtend, summary, attendees, extra = {}) => ({
  dtstart, dtend, summary, calendar_id: 1, organizer: "mailto:org@example.test", creator: OTHER,
  attendee: attendees && attendees.map(([e, s], i) => ({ id: i, attendee: "mailto:" + e, attendee_reply_status: s, attendee_role: 1, attendee_cn: e.split("@")[0] })),
  ...extra,
});

describe("replyStatusOf / busyKind — 什麼算忙碌", () => {
  test("依 attendee_reply_status 對應狀態：0 未回覆 1 已接受 2 已拒絕 3 暫定", () => {
    for (const [code, want] of [[0, "needs"], [1, "accepted"], [2, "declined"], [3, "tentative"]]) {
      assert.equal(gb.replyStatusOf(inst(0, 1, "x", [[ME, code]]), ME), want, `status ${code}`);
    }
  });
  test("不在與會者名單（自建、無與會者）→ own；mailto 與大小寫不影響比對", () => {
    assert.equal(gb.replyStatusOf(inst(0, 1, "yoga", undefined), ME), "own");
    assert.equal(gb.replyStatusOf(inst(0, 1, "x", [["ME@Example.TEST", 1]]), "mailto:" + ME), "accepted");
  });
  test("attendee 若是 JSON 字串也解得開", () => {
    const i = inst(0, 1, "x", [[ME, 3]]); i.attendee = JSON.stringify(i.attendee);
    assert.equal(gb.replyStatusOf(i, ME), "tentative");
  });
  test("已拒絕不算忙碌；暫定另標；跨 24 小時以上是請假/不在；其餘忙碌", () => {
    const s = new Date(2026, 8, 21, 9), e1 = new Date(2026, 8, 21, 10), e2 = new Date(2026, 8, 22, 9);
    assert.equal(gb.busyKind("declined", s, e1), null);
    assert.equal(gb.busyKind("tentative", s, e1), "tentative");
    assert.equal(gb.busyKind("accepted", s, e2), "absent");
    assert.equal(gb.busyKind("needs", s, e1), "busy");
    assert.equal(gb.busyKind("own", s, e1), "busy");
  });
});

describe("toEvent — 時間戳轉換", () => {
  test("dtstart/dtend 直接是 epoch 秒，不加 offset（offset 是字串 '28800' 也不會串接）", () => {
    const ev = gb.toEvent(inst(at(2026, 9, 21, 19), at(2026, 9, 21, 20, 30), "熱舞社", undefined, { offset: "28800" }), OTHER);
    assert.equal(ev.start.getHours(), 19, TZ_NOTE);
    assert.equal(ev.end.getMinutes(), 30);
    assert.ok(!isNaN(ev.start), "不能是 Invalid Date");
    assert.equal(ev.kind, "busy");
  });
  test("沒有 dtend 時用 dtstart 當結束，不會炸", () => {
    const ev = gb.toEvent({ dtstart: at(2026, 9, 21, 9), summary: "" }, OTHER);
    assert.equal(+ev.end, +ev.start);
    assert.equal(ev.summary, "(無標題)");
  });
  test("info 是數字旗標時不會被當成描述物件", () => {
    const ev = gb.toEvent(inst(at(2026, 9, 21, 9), at(2026, 9, 21, 10), "x", undefined, { info: 152 }), OTHER);
    assert.equal(ev.desc, "");
  });
});

describe("fetchSchedule — 排程端點", () => {
  test("URL 對 email 做 encode，帶 starttime/endtime", () => {
    const u = gb.schedUrl("a_b@example.test", 100, 200);
    assert.equal(u, "/cgi-bin/cal/calsrv/schedule/a_b%40example.test/instances?starttime=100&endtime=200");
  });
  test("rspCode 0 → 事件依該人自己的回覆狀態判定", async () => {
    global.fetch = async () => ({ ok: true, json: async () => ({ rspCode: 0, rspMsg: "", instances: [
      inst(at(2026, 9, 21, 9), at(2026, 9, 21, 10), "去", [[OTHER, 1]]),
      inst(at(2026, 9, 21, 11), at(2026, 9, 21, 12), "不去", [[OTHER, 2]]),
    ] }) });
    const r = await gb.fetchSchedule(OTHER, 0, 1);
    assert.equal(r.error, undefined);
    assert.deepEqual(r.events.map((e) => e.kind), ["busy", null]);
  });
  test("rspCode 非 0 → 回 error、不丟例外；-102 是實測的「查無此帳號」", async () => {
    global.fetch = async () => ({ ok: true, json: async () => ({ rspCode: 5, rspMsg: "weird" }) });
    let r = await gb.fetchSchedule("ghost@example.test", 0, 1);
    assert.match(r.error, /rspCode 5.*weird/);
    assert.deepEqual(r.events, []);
    global.fetch = async () => ({ ok: true, json: async () => ({ rspCode: -102, rspMsg: "" }) });
    r = await gb.fetchSchedule("ghost@example.test", 0, 1);
    assert.equal(r.error, "查無此帳號");
  });
  test("HTTP 非 2xx（如未登入被 nginx 擋 410）→ 回 error", async () => {
    global.fetch = async () => ({ ok: false, status: 410, json: async () => { throw new Error("html"); } });
    const r = await gb.fetchSchedule(OTHER, 0, 1);
    assert.match(r.error, /410/);
  });
});

describe("commonFreeSlots — 共同空檔", () => {
  const day = new Date(2026, 8, 21);   // 週一
  const ev = (h1, m1, h2, m2, kind = "busy") => ({ start: new Date(2026, 8, 21, h1, m1), end: new Date(2026, 8, 21, h2, m2), kind });
  const fmt = (slots) => slots.map((s) => `${s.start.getHours()}:${String(s.start.getMinutes()).padStart(2, "0")}-${s.end.getHours()}:${String(s.end.getMinutes()).padStart(2, "0")}`);

  test("沒人忙 → 整段工作時段", () => {
    assert.deepEqual(fmt(gb.commonFreeSlots([[], []], day, 60)), ["9:00-18:00"]);
  });
  test("多人的忙碌合併後扣掉，只留長度夠的", () => {
    const a = [ev(9, 0, 10, 0), ev(14, 0, 15, 0)];
    const b = [ev(10, 0, 10, 30), ev(16, 30, 18, 0)];
    assert.deepEqual(fmt(gb.commonFreeSlots([a, b], day, 60)), ["10:30-14:00", "15:00-16:30"]);
    assert.deepEqual(fmt(gb.commonFreeSlots([a, b], day, 120)), ["10:30-14:00"]);
  });
  test("已拒絕（kind null）不佔時間；暫定佔", () => {
    assert.deepEqual(fmt(gb.commonFreeSlots([[ev(9, 0, 12, 0, null)]], day, 60)), ["9:00-18:00"]);
    assert.deepEqual(fmt(gb.commonFreeSlots([[ev(9, 0, 12, 0, "tentative")]], day, 60)), ["12:00-18:00"]);
  });
  test("工作時段外的事件（早會 7:50、晚上 19:00）不影響；跨日請假吃掉整天", () => {
    assert.deepEqual(fmt(gb.commonFreeSlots([[ev(7, 50, 8, 20), ev(19, 0, 20, 30)]], day, 60)), ["9:00-18:00"]);
    const off = { start: new Date(2026, 8, 20, 8, 30), end: new Date(2026, 8, 30, 18), kind: "absent" };
    assert.deepEqual(gb.commonFreeSlots([[off]], day, 30), []);
  });
  test("只算給定那一天，別天的事件不算", () => {
    const tomorrow = { start: new Date(2026, 8, 22, 9), end: new Date(2026, 8, 22, 18), kind: "busy" };
    assert.deepEqual(fmt(gb.commonFreeSlots([[tomorrow]], day, 60)), ["9:00-18:00"]);
  });
});

describe("workWeek / durationFromForm", () => {
  test("任一天都回該週的週一到週五", () => {
    for (const d of ["2026-09-21", "2026-09-23", "2026-09-27"]) {   // 一、三、日
      const w = gb.workWeek(d);
      assert.equal(w.length, 5);
      assert.equal(w[0].getDay(), 1, d + " 應從週一起");
      assert.equal(w[4].getDay(), 5);
    }
    assert.equal(gb.workWeek("2026-09-27")[0].getDate(), 21, "週日屬於前一個週一開始的那週");
  });
  test("日期壞掉或空白時退回今天所在週", () => {
    assert.equal(gb.workWeek("").length, 5);
    assert.equal(gb.workWeek("not-a-date").length, 5);
  });
  test("會議長度取表單差值，缺或倒退時用預設 60", () => {
    assert.equal(gb.durationFromForm("09:00", "10:30"), 90);
    assert.equal(gb.durationFromForm("", "10:30"), gb.DEFAULT_DURATION_MIN);
    assert.equal(gb.durationFromForm("11:00", "10:00"), gb.DEFAULT_DURATION_MIN);
  });
});

describe("mapLimit — 併發上限", () => {
  test("同時進行的數量不超過上限，且結果順序與輸入一致", async () => {
    let running = 0, peak = 0;
    const items = Array.from({ length: 10 }, (_, i) => i);
    const out = await gb.mapLimit(items, gb.FETCH_LIMIT, async (i) => {
      running++; peak = Math.max(peak, running);
      await new Promise((r) => realSetTimeout(r, 5 - (i % 3)));   // 完成順序打亂
      running--;
      return i * 2;
    });
    assert.equal(peak, gb.FETCH_LIMIT);
    assert.deepEqual(out, items.map((i) => i * 2));
  });
  test("空清單直接回空陣列", async () => {
    assert.deepEqual(await gb.mapLimit([], 4, async () => 1), []);
  });
});

describe("renderTimeline — 時間軸 HTML", () => {
  const days = () => gb.workWeek("2026-09-21");   // gb 在 beforeEach 才就位
  const person = (email, events, self = false, error) => ({ email, name: email.split("@")[0], self, events, error });
  const ev = (d, h1, h2, kind, summary) => ({ start: new Date(2026, 8, d, h1), end: new Date(2026, 8, d, h2), kind, summary, status: "accepted" });

  test("第一列共同空檔帶 data-date/start/end 供點擊填表；自己標 (我)", () => {
    const html = gb.renderTimeline([person(ME, [ev(21, 9, 12, "busy", "A")], true), person(OTHER, [], false)], days(), 60, []);
    assert.match(html, /tl-free[^>]*data-date="2026-09-21" data-start="12:00" data-end="18:00"/);
    assert.match(html, /\(我\)/);
    assert.match(html, /≥ 60 分/);
  });
  test("忙碌／暫定／請假各有自己的 class，標題會顯示且經過跳脫；已拒絕不畫", () => {
    const html = gb.renderTimeline([person(OTHER, [
      ev(21, 9, 10, "busy", "<b>x</b>"), ev(22, 9, 10, "tentative", "T"), ev(23, 9, 10, "absent", "OFF"), ev(24, 9, 10, null, "declined"),
    ])], days(), 60, []);
    assert.match(html, /tl-blk tl-busy[^>]*>[^<]*&lt;b&gt;x&lt;\/b&gt;/);
    assert.match(html, /tl-tentative/); assert.match(html, /tl-absent/);
    assert.doesNotMatch(html, /declined/);
  });
  test("超過人數上限會提示並列出未列入的人；查不到的人在該列註明原因", () => {
    const html = gb.renderTimeline([person(OTHER, [], false, "HTTP 410")], days(), 60, ["x@example.test", "y@example.test"]);
    assert.match(html, new RegExp(`超過 ${gb.MAX_PEOPLE} 人`));
    assert.match(html, /x@example.test, y@example.test/);
    assert.match(html, /查不到：HTTP 410/);
  });
  test("時間軸只畫 08–19；整天外的事件不會超出帶子（left/width 夾在 0–100）", () => {
    const html = gb.renderTimeline([person(OTHER, [ev(21, 5, 7, "busy", "early"), ev(21, 20, 23, "busy", "late")])], days(), 60, []);
    assert.doesNotMatch(html, /early|late/, "完全在視窗外的事件不畫");
  });
});

describe("renderTimeline — 共同空檔的可信度", () => {
  const days = () => gb.workWeek("2026-09-21");
  const person = (email, events, error) => ({ email, name: email.split("@")[0], self: false, events, error });
  test("有人查不到 → 空檔改畫灰色、沒有 data-* 不可點，並標出未計入人數", () => {
    const html = gb.renderTimeline([person(ME, []), person(OTHER, [], "HTTP 410")], days(), 60, []);
    assert.doesNotMatch(html, /tl-free[^>]*data-date=/, "不能出現可點的綠色空檔");
    assert.match(html, /tl-unsure/);
    assert.match(html, /1 人未計入，不可信/);
  });
  test("被人數上限截掉的人也算未計入", () => {
    const html = gb.renderTimeline([person(ME, [])], days(), 60, ["a@example.test", "b@example.test"]);
    assert.doesNotMatch(html, /tl-free[^>]*data-date=/);
    assert.match(html, /2 人未計入/);
    assert.match(html, /共同空檔不含他們/);
  });
  test("全員都查到才有可點的綠色空檔", () => {
    const html = gb.renderTimeline([person(ME, []), person(OTHER, [])], days(), 60, []);
    assert.match(html, /tl-free[^>]*data-date="2026-09-21"/);
    assert.doesNotMatch(html.split("tl-legend")[0], /tl-unsure/, "時間軸本體（圖例除外）不該有灰色空檔");
  });
  test("跨日請假在每個日欄的標題都寫整段日期，不是誤導的 08:30–18:00", () => {
    const off = { start: new Date(2026, 8, 21, 8, 30), end: new Date(2026, 8, 23, 18), kind: "absent", summary: "OFF" };
    const html = gb.renderTimeline([person(OTHER, [off])], days(), 60, []);
    const titles = [...html.matchAll(/tl-absent[^>]*title="([^"]+)"/g)].map((m) => m[1]);
    assert.equal(titles.length, 3, "週一到週三各一塊");
    titles.forEach((t) => assert.match(t, /請假\/不在（9\/21–9\/23）OFF/));
  });
  test("跨午夜的一般事件在第二天日欄標題從 00:00 起算", () => {
    const late = { start: new Date(2026, 8, 21, 22), end: new Date(2026, 8, 22, 10), kind: "busy", summary: "夜班" };
    const html = gb.renderTimeline([person(OTHER, [late])], days(), 60, []);
    assert.match(html, /title="00:00–10:00 夜班"/);
  });
});

describe("toEvent — 沒有 dtend 的事件", () => {
  test("hasEnd=false，看板只顯示單一時間", () => {
    const ev = gb.toEvent({ dtstart: at(2026, 9, 21, 9), summary: "提醒" }, OTHER);
    assert.equal(ev.hasEnd, false);
  });
});

describe("selfEmail — 自己是誰", () => {
  const LS = () => dom.window.localStorage;
  let prompts;
  beforeEach(() => { try { LS().clear(); } catch (_) {} global.localStorage = LS(); prompts = 0; global.fetch = async () => { throw new Error("不該發任何請求"); }; });

  test("第一次問一次、修剪空白與大小寫、記住", async () => {
    global.prompt = () => { prompts++; return " Me@Example.TEST "; };
    assert.equal(await gb.selfEmail(), "me@example.test");
    assert.equal(LS().getItem("m2k.selfEmail"), "me@example.test");
    assert.equal(prompts, 1);
  });
  test("按取消或亂打 → 回 null、不寫入", async () => {
    global.prompt = () => null;
    assert.equal(await gb.selfEmail(), null);
    global.prompt = () => "not an email";
    assert.equal(await gb.selfEmail(), null);
    assert.equal(LS().getItem("m2k.selfEmail"), null);
  });
  test("有記住就不再問；resetSelf 後會重新問", async () => {
    global.prompt = () => { prompts++; return "me@example.test"; };
    await gb.selfEmail();
    gb._reset();
    assert.equal(await gb.selfEmail(), "me@example.test");
    assert.equal(prompts, 1, "第二次不該再問");
    gb.resetSelf();
    assert.equal(LS().getItem("m2k.selfEmail"), null);
    await gb.selfEmail();
    assert.equal(prompts, 2, "重設後要再問一次");
  });
});

describe("chip 名稱選擇器 — 惡意 data-id 不會炸掉時間軸", () => {
  test("data-id 含引號時 showSlots 用到的選擇器不丟 SyntaxError", () => {
    const list = dom.window.document.querySelector(".scheduleAttendeeList");
    const chip = dom.window.document.createElement("div"); chip.className = "scheduleAttendee"; chip.setAttribute("data-id", 'evil"]@x'); chip.textContent = "Evil";
    list.appendChild(chip);
    // 與腳本內 chipName 相同的跳脫方式
    const sel = `.scheduleAttendeeList .scheduleAttendee[data-id="${'evil"]@x'.replace(/["\\]/g, "\\$&")}" i]`;
    assert.doesNotThrow(() => dom.window.document.querySelector(sel));
    assert.equal(dom.window.document.querySelector(sel).textContent, "Evil");
  });
});

describe("renderTimeline — 請假結束在午夜整點", () => {
  test("9/21 00:00 到 9/23 00:00 顯示為 9/21–9/22", () => {
    const off = { start: new Date(2026, 8, 21), end: new Date(2026, 8, 23), kind: "absent", summary: "OFF" };
    const html = gb.renderTimeline([{ email: OTHER, name: "o", self: false, events: [off] }], gb.workWeek("2026-09-21"), 60, []);
    const titles = [...html.matchAll(/tl-absent[^>]*title="([^"]+)"/g)].map((m) => m[1]);
    assert.equal(titles.length, 2, "只該畫在 9/21、9/22 兩欄");
    titles.forEach((x) => assert.equal(x, "請假/不在（9/21–9/22）OFF"));
  });
});

describe("fetchFeedEvents — 看板 feeds 端點", () => {
  test("同樣不加 offset；沒 error 欄位就當成功", async () => {
    global.fetch = async () => ({ ok: true, json: async () => ({ instances: [{ dtstart: at(2026, 9, 21, 17, 30), dtend: at(2026, 9, 21, 18), summary: "晚餐", offset: "28800" }] }) });
    const evs = await gb.fetchFeedEvents({ feeds: "default", id: "1", name: "我", color: "#000" }, 0, 1);
    assert.equal(evs.length, 1);
    assert.equal(evs[0].start.getHours(), 17, TZ_NOTE);
    assert.equal(evs[0].calName, "我");
  });
  test("端點失敗回空陣列，不讓整個看板掛掉", async () => {
    global.fetch = async () => { throw new Error("boom"); };
    assert.deepEqual(await gb.fetchFeedEvents({ feeds: "default", id: "1", name: "我" }, 0, 1), []);
  });
});

describe("buildPanel — UI 冒煙（不走 module.exports 掛鉤，讓腳本真的建面板）", () => {
  test("面板、按鈕、兩個頁籤都建得起來；按鈕第一次點是「開」；切到看板會載入行事曆清單", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync(SCRIPT, "utf8");
    const d2 = new JSDOM(`<body><div class="scheduleAttendeeList"></div><input class="scheduleAttendeeInput"></body>`,
      { url: "https://mail.gss.com.tw/cgi-bin/cal/x", runScripts: "outside-only" });
    let feedsCalls = 0;
    d2.window.fetch = async () => { feedsCalls++; return { ok: true, json: async () => ({ calendars: [{ id: 7, display_name: "我的", color: 1 }] }), text: async () => "" }; };
    d2.window.eval(src);
    await new Promise((r) => realSetTimeout(r, 900));   // 腳本每 800ms 探一次 document.body
    const doc = d2.window.document;
    for (const id of ["m2k-btn", "m2k-panel", "m2k-overlay", "gb-slots", "gb-book", "bd-gen", "m2k-cals"]) assert.ok(doc.getElementById(id), id);
    doc.getElementById("m2k-btn").click();
    assert.equal(doc.getElementById("m2k-panel").style.display, "block", "第一次點要打開，不是關上");
    doc.querySelector('.m2k-tab[data-tab="board"]').click();
    doc.querySelector('.m2k-tab[data-tab="board"]').click();   // 連點兩次只該打一輪 feeds
    await new Promise((r) => realSetTimeout(r, 20));
    assert.match(doc.getElementById("m2k-cals").textContent, /我的/);
    assert.equal(feedsCalls, 3, "default/subscribed/public 各一支，不重複");
    d2.window.close();
  });
});
