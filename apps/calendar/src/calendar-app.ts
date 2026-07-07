/**
 * m2k 行事曆 MCP App — 週/月檢視、事件詳情、新增與編輯會議。
 * 資料流：show_calendar（初始）→ calendar_data（翻頁/重整，app-only）；
 * 異動走既有 book / update_event 工具，成功後重新抓資料。
 * 時間一律台北牆鐘字串（"YYYY-MM-DD HH:MM"），避免時區換算歧義。
 */
import {
  App,
  applyDocumentTheme,
  applyHostFonts,
  applyHostStyleVariables,
  type McpUiHostContext,
} from "@modelcontextprotocol/ext-apps";
import "./calendar-app.css";

interface Attendee { name: string; email: string; partstat: string }
interface Ev {
  uid: string; summary: string; start: string; end: string; allday: boolean;
  location: string; description: string; organizer: string; rrule: string;
  attendees: Attendee[];
}
interface CalData { range: { start: string; end: string }; today: string; me?: string; events: Ev[] }

const WK = ["日", "一", "二", "三", "四", "五", "六"];
const HOUR_H = 44;          // 週檢視每小時高度(px)
const DAY_START_SCROLL = 8; // 預設捲到 08:00

let view: "week" | "month" = "week";
let anchor = new Date();    // 目前檢視的錨點日期
let data: CalData | null = null;
let loading = false;

const root = document.getElementById("root")!;
const app = new App({ name: "m2k Calendar", version: "1.0.0" });

// ---------- 日期工具 ----------
const pad = (n: number) => String(n).padStart(2, "0");
const dstr = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parseDT = (s: string): Date => {
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/);
  if (!m) return new Date(NaN);
  return new Date(+m[1], +m[2] - 1, +m[3], +(m[4] ?? 0), +(m[5] ?? 0));
};
const addDays = (d: Date, n: number) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const weekStart = (d: Date) => addDays(d, -d.getDay()); // 週日起
const sameDay = (a: Date, b: Date) => dstr(a) === dstr(b);
const hhmm = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const toLocalInput = (d: Date) => `${dstr(d)}T${hhmm(d)}`;
const fromLocalInput = (s: string) => s.replace("T", " ");

function rangeFor(v: typeof view, a: Date): { start: Date; end: Date } {
  if (v === "week") { const s = weekStart(a); return { start: s, end: addDays(s, 7) }; }
  const first = new Date(a.getFullYear(), a.getMonth(), 1);
  const s = weekStart(first);
  return { start: s, end: addDays(s, 42) };
}

// ---------- 資料 ----------
async function fetchData(): Promise<void> {
  const { start, end } = rangeFor(view, anchor);
  loading = true; render();
  try {
    const res = await app.callServerTool({
      name: "calendar_data",
      arguments: { start: dstr(start), end: dstr(end) },
    });
    const sc = res.structuredContent as (CalData & { error?: string }) | undefined;
    if (sc?.error) toast("讀取失敗：" + sc.error, true);
    else if (sc?.events) data = sc;
    else toast("讀取失敗：" + firstText(res), true);
  } catch (e) {
    toast("讀取失敗：" + String(e), true);
  } finally {
    loading = false; render();
  }
}

function firstText(res: unknown): string {
  const c = (res as { content?: Array<{ type: string; text?: string }> })?.content;
  return c?.find((x) => x.type === "text")?.text?.slice(0, 200) ?? "";
}

// ---------- 畫面 ----------
function render() {
  root.innerHTML = "";
  const wrap = el("div", "cal");
  wrap.appendChild(renderHeader());
  const body = el("div", "cal-body");
  if (loading && !data) body.appendChild(el("div", "cal-loading", "載入中…"));
  else if (view === "week") body.appendChild(renderWeek());
  else body.appendChild(renderMonth());
  wrap.appendChild(body);
  root.appendChild(wrap);
  if (view === "week") {
    const grid = root.querySelector(".wk-scroll");
    if (grid) grid.scrollTop = HOUR_H * DAY_START_SCROLL - 8;
  }
}

function el(tag: string, cls?: string, text?: string): HTMLElement {
  const x = document.createElement(tag);
  if (cls) x.className = cls;
  if (text !== undefined) x.textContent = text;
  return x;
}

function renderHeader(): HTMLElement {
  const h = el("div", "cal-hdr");
  const nav = el("div", "cal-nav");
  const btn = (label: string, fn: () => void, cls = "btn") => {
    const b = el("button", cls, label) as HTMLButtonElement;
    b.onclick = fn; return b;
  };
  nav.append(
    btn("‹", () => { anchor = addDays(anchor, view === "week" ? -7 : -30); fetchData(); }),
    btn("今天", () => { anchor = new Date(); fetchData(); }),
    btn("›", () => { anchor = addDays(anchor, view === "week" ? 7 : 30); fetchData(); }),
  );
  const { start, end } = rangeFor(view, anchor);
  const title = el("div", "cal-title",
    view === "week"
      ? `${start.getFullYear()}年${start.getMonth() + 1}月 ${start.getDate()}日 – ${addDays(end, -1).getMonth() + 1}月${addDays(end, -1).getDate()}日`
      : `${anchor.getFullYear()}年${anchor.getMonth() + 1}月`);
  const right = el("div", "cal-nav");
  const wk = btn("週", () => { view = "week"; fetchData(); }, view === "week" ? "btn on" : "btn");
  const mo = btn("月", () => { view = "month"; fetchData(); }, view === "month" ? "btn on" : "btn");
  right.append(wk, mo, btn("＋ 新增", () => openForm(null), "btn primary"));
  h.append(nav, title, right);
  return h;
}

function eventsOfDay(d: Date): Ev[] {
  if (!data) return [];
  return data.events
    .filter((e) => {
      const s = parseDT(e.start);
      const en = e.end ? parseDT(e.end) : s;
      const d0 = new Date(d); d0.setHours(0, 0, 0, 0);
      const d1 = addDays(d0, 1);
      return s < d1 && en > d0;
    })
    .sort((a, b) => a.start.localeCompare(b.start));
}

// 重疊事件分欄（同組事件均分寬度）
function layoutDay(evs: Ev[]): Array<{ ev: Ev; col: number; cols: number }> {
  const items = evs.filter((e) => !e.allday).map((e) => ({
    ev: e, s: parseDT(e.start).getTime(), e2: parseDT(e.end || e.start).getTime(), col: 0, cols: 1,
  }));
  items.sort((a, b) => a.s - b.s);
  const active: typeof items = [];
  let group: typeof items = [];
  let groupEnd = -1;
  const flush = () => {
    const n = Math.max(1, ...group.map((g) => g.col + 1));
    group.forEach((g) => (g.cols = n));
    group = [];
  };
  for (const it of items) {
    if (group.length && it.s >= groupEnd) flush();
    for (let i = active.length - 1; i >= 0; i--) if (active[i].e2 <= it.s) active.splice(i, 1);
    const used = new Set(active.map((a) => a.col));
    let c = 0; while (used.has(c)) c++;
    it.col = c; active.push(it); group.push(it);
    groupEnd = Math.max(groupEnd, it.e2);
  }
  if (group.length) flush();
  return items.map(({ ev, col, cols }) => ({ ev, col, cols }));
}

function renderWeek(): HTMLElement {
  const { start } = rangeFor("week", anchor);
  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i));
  const today = data ? parseDT(data.today) : new Date();

  const cont = el("div", "wk");
  // 星期列 + 全天列
  const head = el("div", "wk-head");
  head.appendChild(el("div", "wk-gutter"));
  for (const d of days) {
    const c = el("div", "wk-day-h" + (sameDay(d, today) ? " today" : ""));
    c.append(el("div", "wk-dow", `週${WK[d.getDay()]}`), el("div", "wk-date", String(d.getDate())));
    head.appendChild(c);
  }
  cont.appendChild(head);

  const allday = el("div", "wk-allday");
  allday.appendChild(el("div", "wk-gutter", "全天"));
  for (const d of days) {
    const c = el("div", "wk-allday-cell");
    for (const e of eventsOfDay(d).filter((x) => x.allday)) {
      const chip = el("div", "chip", e.summary);
      chip.onclick = () => openDetail(e);
      c.appendChild(chip);
    }
    allday.appendChild(c);
  }
  cont.appendChild(allday);

  const scroll = el("div", "wk-scroll");
  const grid = el("div", "wk-grid");
  grid.style.height = `${24 * HOUR_H}px`;
  const gutter = el("div", "wk-gutter");
  for (let h = 0; h < 24; h++) {
    const lab = el("div", "wk-hour", `${pad(h)}:00`);
    lab.style.top = `${h * HOUR_H}px`;
    gutter.appendChild(lab);
  }
  grid.appendChild(gutter);

  for (const d of days) {
    const col = el("div", "wk-col" + (sameDay(d, today) ? " today" : ""));
    for (let h = 1; h < 24; h++) {
      const line = el("div", "wk-line");
      line.style.top = `${h * HOUR_H}px`;
      col.appendChild(line);
    }
    // 點空白處 → 以該時段開新會議
    col.onclick = (evt) => {
      if ((evt.target as HTMLElement) !== col) return;
      const y = evt.offsetY;
      const hour = Math.floor(y / HOUR_H);
      const s = new Date(d); s.setHours(hour, 0, 0, 0);
      openForm(null, s);
    };
    const d0 = new Date(d); d0.setHours(0, 0, 0, 0);
    for (const { ev, col: ci, cols } of layoutDay(eventsOfDay(d))) {
      const s = parseDT(ev.start); const e2 = parseDT(ev.end || ev.start);
      const top = Math.max(0, (Math.max(s.getTime(), d0.getTime()) - d0.getTime()) / 3600000 * HOUR_H);
      const bot = Math.min(24 * HOUR_H,
        (Math.min(e2.getTime(), addDays(d0, 1).getTime()) - d0.getTime()) / 3600000 * HOUR_H);
      const b = el("div", "wk-ev");
      b.style.top = `${top}px`;
      b.style.height = `${Math.max(18, bot - top - 2)}px`;
      b.style.left = `calc(${(ci / cols) * 100}% + 1px)`;
      b.style.width = `calc(${(1 / cols) * 100}% - 3px)`;
      b.append(el("div", "wk-ev-t", `${hhmm(s)} ${ev.summary}`));
      if (ev.location) b.append(el("div", "wk-ev-l", `@${ev.location}`));
      b.onclick = (x) => { x.stopPropagation(); openDetail(ev); };
      col.appendChild(b);
    }
    grid.appendChild(col);
  }
  scroll.appendChild(grid);
  cont.appendChild(scroll);
  return cont;
}

function renderMonth(): HTMLElement {
  const { start } = rangeFor("month", anchor);
  const today = data ? parseDT(data.today) : new Date();
  const cont = el("div", "mo");
  const head = el("div", "mo-head");
  for (const w of WK) head.appendChild(el("div", "mo-dow", `週${w}`));
  cont.appendChild(head);
  const grid = el("div", "mo-grid");
  for (let i = 0; i < 42; i++) {
    const d = addDays(start, i);
    const cell = el("div", "mo-cell"
      + (d.getMonth() !== anchor.getMonth() ? " other" : "")
      + (sameDay(d, today) ? " today" : ""));
    const num = el("div", "mo-num", String(d.getDate()));
    num.onclick = () => { anchor = d; view = "week"; fetchData(); };
    cell.appendChild(num);
    const evs = eventsOfDay(d);
    for (const e of evs.slice(0, 3)) {
      const chip = el("div", "chip" + (e.allday ? " allday" : ""),
        (e.allday ? "" : hhmm(parseDT(e.start)) + " ") + e.summary);
      chip.onclick = () => openDetail(e);
      cell.appendChild(chip);
    }
    if (evs.length > 3) {
      const more = el("div", "mo-more", `還有 ${evs.length - 3} 筆…`);
      more.onclick = () => { anchor = d; view = "week"; fetchData(); };
      cell.appendChild(more);
    }
    grid.appendChild(cell);
  }
  cont.appendChild(grid);
  return cont;
}

// ---------- 詳情 / 表單 ----------
function overlay(): HTMLElement {
  const ov = el("div", "ov");
  ov.onclick = (e) => { if (e.target === ov) ov.remove(); };
  root.appendChild(ov);
  return ov;
}

function openDetail(ev: Ev) {
  const ov = overlay();
  const card = el("div", "card");
  card.append(el("h2", "", ev.summary));
  const meta = el("div", "meta");
  const s = parseDT(ev.start); const e2 = ev.end ? parseDT(ev.end) : s;
  meta.append(el("div", "", ev.allday
    ? `📅 ${dstr(s)}（全天）`
    : `🕐 ${dstr(s)}（週${WK[s.getDay()]}） ${hhmm(s)} – ${sameDay(s, e2) ? "" : dstr(e2) + " "}${hhmm(e2)}`));
  if (ev.rrule) meta.append(el("div", "", `⟳ ${ev.rrule}`));
  if (ev.location) meta.append(el("div", "", `📍 ${ev.location}`));
  if (ev.organizer) meta.append(el("div", "", `👤 召集人：${ev.organizer}`));
  card.appendChild(meta);
  if (ev.attendees.length) {
    const box = el("div", "atts");
    box.append(el("div", "atts-h", `與會者 ${ev.attendees.length} 人`));
    const sym: Record<string, string> = { ACCEPTED: "✓", DECLINED: "✗", TENTATIVE: "?" };
    for (const a of ev.attendees)
      box.append(el("div", "att", `${sym[a.partstat] ?? "·"} ${a.name} <${a.email}>`));
    card.appendChild(box);
  }
  // 我的出席回覆（只更新自己日曆的狀態，不會通知召集人）
  const mine = data?.me
    ? ev.attendees.find((a) => a.email.toLowerCase() === data!.me!.toLowerCase())
    : undefined;
  if (mine) {
    const row = el("div", "rsvp");
    row.append(el("span", "flab", "我的回覆"));
    const opts: Array<[string, string, string]> = [
      ["accept", "ACCEPTED", "✓ 接受"], ["tentative", "TENTATIVE", "? 暫定"],
      ["decline", "DECLINED", "✗ 拒絕"]];
    for (const [arg, st, label] of opts) {
      const b = el("button", "btn" + (mine.partstat === st ? " on" : ""), label) as HTMLButtonElement;
      b.onclick = async () => {
        b.disabled = true;
        const res = await app.callServerTool({ name: "respond_event",
          arguments: { uid: ev.uid, response: arg } }).catch((e) => ({ content: [{ type: "text", text: "錯誤：" + e }] }));
        const txt = firstText(res);
        if (txt.startsWith("錯誤")) { toast(txt, true); b.disabled = false; return; }
        toast("已更新出席狀態（不會通知召集人）");
        card.closest(".ov")?.remove();
        fetchData();
      };
      row.appendChild(b);
    }
    card.appendChild(row);
  }
  // 快速加入其他與會者
  {
    const row = el("div", "rsvp");
    const inp = el("input") as HTMLInputElement;
    inp.type = "text"; inp.placeholder = "加入與會者 email（逗號可多位）";
    const add = el("button", "btn", "加入") as HTMLButtonElement;
    add.onclick = async () => {
      const emails = inp.value.split(/[\s,;、]+/).map((x) => x.trim()).filter(Boolean);
      if (!emails.length) return;
      add.disabled = true; add.textContent = "處理中…";
      const res = await app.callServerTool({ name: "update_event",
        arguments: { uid: ev.uid, add_attendees: emails } }).catch((e) => ({ content: [{ type: "text", text: "錯誤：" + e }] }));
      const txt = firstText(res);
      if (txt.startsWith("錯誤")) { toast(txt, true); add.disabled = false; add.textContent = "加入"; return; }
      toast("已加入（不會自動寄通知信）");
      card.closest(".ov")?.remove();
      fetchData();
    };
    row.append(inp, add);
    card.appendChild(row);
  }
  if (ev.description) {
    const d = el("div", "desc"); d.textContent = ev.description;
    card.appendChild(d);
  }
  const acts = el("div", "acts");
  const edit = el("button", "btn primary", "編輯") as HTMLButtonElement;
  edit.onclick = () => { ov.remove(); openForm(ev); };
  const close = el("button", "btn", "關閉") as HTMLButtonElement;
  close.onclick = () => ov.remove();
  acts.append(edit, close);
  card.appendChild(acts);
  ov.appendChild(card);
}

function openForm(ev: Ev | null, presetStart?: Date) {
  const ov = overlay();
  const card = el("div", "card");
  card.append(el("h2", "", ev ? "編輯會議" : "新增會議"));
  const f = (label: string, input: HTMLElement) => {
    const row = el("label", "frow");
    row.append(el("span", "flab", label), input);
    return row;
  };
  const inp = (type: string, value = "") => {
    const i = el("input") as HTMLInputElement;
    i.type = type; i.value = value; return i;
  };
  const s0 = presetStart ?? (() => { const d = new Date(); d.setHours(d.getHours() + 1, 0, 0, 0); return d; })();
  const title = inp("text", ev?.summary ?? "");
  const start = inp("datetime-local", ev ? toLocalInput(parseDT(ev.start)) : toLocalInput(s0));
  const end = inp("datetime-local", ev?.end ? toLocalInput(parseDT(ev.end)) : toLocalInput(new Date(s0.getTime() + 3600000)));
  const loc = inp("text", ev?.location ?? "");
  const desc = el("textarea") as HTMLTextAreaElement;
  desc.value = ev?.description ?? "";
  const atts = el("textarea") as HTMLTextAreaElement;
  atts.placeholder = "email，逗號或換行分隔";
  atts.value = ev ? ev.attendees.map((a) => a.email).join(", ") : "";
  card.append(f("標題", title), f("開始", start), f("結束", end), f("地點", loc),
    f("描述", desc), f("與會者", atts));
  const note = el("div", "note", "註：CalDAV 無排程，異動不會自動寄通知信。");
  card.appendChild(note);
  const acts = el("div", "acts");
  const save = el("button", "btn primary", ev ? "儲存變更" : "建立") as HTMLButtonElement;
  const cancel = el("button", "btn", "取消") as HTMLButtonElement;
  cancel.onclick = () => ov.remove();
  save.onclick = async () => {
    if (!title.value.trim() || !start.value) { toast("標題與開始時間必填", true); return; }
    save.disabled = true; save.textContent = "處理中…";
    const emails = atts.value.split(/[\s,;、]+/).map((x) => x.trim()).filter(Boolean);
    try {
      let res;
      if (!ev) {
        res = await app.callServerTool({ name: "book", arguments: {
          title: title.value.trim(), start: fromLocalInput(start.value),
          end: end.value ? fromLocalInput(end.value) : "",
          location: loc.value.trim(), description: desc.value, attendees: emails,
        }});
      } else {
        const old = new Set(ev.attendees.map((a) => a.email.toLowerCase()));
        const now = new Set(emails.map((x) => x.toLowerCase()));
        const args: Record<string, unknown> = { uid: ev.uid };
        if (title.value.trim() !== ev.summary) args.title = title.value.trim();
        if (fromLocalInput(start.value) !== ev.start.slice(0, 16)) args.start = fromLocalInput(start.value);
        if (end.value && fromLocalInput(end.value) !== ev.end.slice(0, 16)) args.end = fromLocalInput(end.value);
        if (loc.value.trim() && loc.value.trim() !== ev.location) args.location = loc.value.trim();
        if (desc.value && desc.value !== ev.description) args.description = desc.value;
        const add = emails.filter((x) => !old.has(x.toLowerCase()));
        const rem = ev.attendees.map((a) => a.email).filter((x) => !now.has(x.toLowerCase()));
        if (add.length) args.add_attendees = add;
        if (rem.length) args.remove_attendees = rem;
        if (Object.keys(args).length === 1) { toast("沒有任何變更"); ov.remove(); return; }
        res = await app.callServerTool({ name: "update_event", arguments: args });
      }
      const txt = firstText(res);
      if (txt.startsWith("錯誤")) { toast(txt, true); save.disabled = false; save.textContent = ev ? "儲存變更" : "建立"; return; }
      toast(ev ? "已更新" : "已建立");
      ov.remove();
      fetchData();
    } catch (e) {
      toast(String(e), true);
      save.disabled = false; save.textContent = ev ? "儲存變更" : "建立";
    }
  };
  acts.append(save, cancel);
  card.appendChild(acts);
  ov.appendChild(card);
  title.focus();
}

let toastTimer: ReturnType<typeof setTimeout> | undefined;
function toast(msg: string, isErr = false) {
  document.querySelector(".toast")?.remove();
  const t = el("div", "toast" + (isErr ? " err" : ""), msg);
  document.body.appendChild(t);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), isErr ? 6000 : 2500);
}

// ---------- MCP App 生命週期 ----------
function handleHostContext(ctx: McpUiHostContext) {
  if (ctx.theme) applyDocumentTheme(ctx.theme);
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
  if (ctx.styles?.css?.fonts) applyHostFonts(ctx.styles.css.fonts);
  if (ctx.safeAreaInsets) {
    const { top, right, bottom, left } = ctx.safeAreaInsets;
    document.body.style.padding = `${top}px ${right}px ${bottom}px ${left}px`;
  }
}

app.onhostcontextchanged = handleHostContext;
app.onerror = console.error;
app.onteardown = async () => ({});

app.ontoolinput = (params) => {
  // 任何行事曆工具（show_calendar/agenda/list_events/book…）的參數決定初始檢視範圍
  const a = params.arguments as { start?: string; end?: string; days?: number } | undefined;
  if (a?.start) {
    const d = parseDT(a.start);
    if (!isNaN(d.getTime())) anchor = d;
  }
  let span = a?.days ?? 0;
  if (a?.start && a?.end) {
    const s = parseDT(a.start), e = parseDT(a.end);
    if (!isNaN(s.getTime()) && !isNaN(e.getTime()))
      span = Math.round((e.getTime() - s.getTime()) / 86400000);
  }
  if (span > 10) view = "month";
  loading = true; render();
};

app.ontoolresult = (result) => {
  const sc = result.structuredContent as (CalData & { error?: string }) | undefined;
  if (sc?.error) { loading = false; render(); toast("讀取失敗：" + sc.error, true); }
  else if (sc?.events) { data = sc; loading = false; render(); }
  else fetchData();
};

app.connect().then(() => {
  const ctx = app.getHostContext();
  if (ctx) handleHostContext(ctx);
  render();
  // 若 host 未以工具啟動（直接開資源），自行抓當週資料
  setTimeout(() => { if (!data && !loading) fetchData(); }, 800);
});
