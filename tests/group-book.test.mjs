/* m2k-group-book.user.js 的離線測試 — 不連伺服器、不需登入。
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
const SCRIPT = path.resolve(import.meta.dirname, "../userscripts/m2k-group-book.user.js");

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
