---
status: accepted
---

# 會議寫入改走 webmail 原生 calsrv API，CalDAV 只留讀取

實測發現：經 CalDAV PUT 建的會議只存在召集人自己的行事曆。站台 CalDAV 沒有排程
（schedule-outbox 404），ATTENDEE 只是資料；另外寄的 iMIP 邀請信收件端也不會自動加入行事曆。
結果是 `book` 回「已建立」，與會者卻完全看不到這場會。

webmail 自己用的 calsrv API（`/cgi-bin/cal/calsrv/feeds/default/default/{calendar_id}/events/`，
POST 建立、PUT `{id}` 修改、DELETE `{id}` 刪除，form-urlencoded，只需 ADR-0003 換到的
`key` cookie）在帶 `send_meeting_mail=true` 時，伺服器會把會議直接寫進每位與會者的行事曆
（未回覆狀態）並寄出通知信；召集人刪除時也會一併從與會者那邊移除。帶 `false` 則和 CalDAV
一樣只寫自己。**決定性的變因是 send_meeting_mail，不是「原生 vs CalDAV」本身。**

因此：

- `book` / `update_event` / `delete_event` 的寫入一律走原生 API，CalDAV 只留讀取。
- 有與會者就帶 `send_meeting_mail=true`——帶了與會者就是要邀請他們。自己組 iMIP 信寄出的
  那條路（`notify` 參數、`send_invite`）移除，由伺服器寄信。`M2K_DISABLE_NOTIFY` 環境變數
  也一併移除：寫進對方行事曆只有這一條路，關掉等於建一場對方看不到、不會來參加的會，
  這個開關沒有有意義的用途。
- 對外的事件識別仍是 iCalendar UID（agenda / list_events 顯示的 id 不變），寫入前在內部
  對應到原生的數字 id。CalDAV 建的舊會議在原生 API 裡同樣有數字 id，可以照改。
- 重複會議三種範圍沿用原生前端的做法：只改這次＝原系列加 exdate＋新建事件
  （`modify_recur=1`、`modified_exdate`）；改此次及以後＝截斷原系列（until 或 count）＋新建系列
  （`modify_recur=2`）；全部＝直接 PUT。
- 原生 API 不收 `url`（伺服器忽略，實測），會議連結改寫在描述第一行。

欄位的序列化以 webmail 前端 `calendar.js` 的 `eventEditObject.toRequestData` 為準
（重複：freq/interval/by_day/by_setpos/by_monthday/count/until；提醒：alarm_trigger{n}=`-PT{秒}S`；
全天：dtstart/dtend 為 `yyyyMMdd`、dtend 為排他日；時間：`yyyyMMdd'T'HHmmss` 當地時間＋
`timezone_dtstart=Asia/Taipei@28800`）。

## Considered Options

- 維持 CalDAV，另寄 iMIP：已實測收件端不會加入行事曆，不解決問題。
- 有與會者才走原生、個人行程留在 CalDAV：兩個寫入 adapter 並存，多一份要維護的
  iCalendar 組裝與驗證，換到的只是少數情境；不採用。
- 保留 `notify` 預設 false：預設就是「對方看不到」，正是這次的坑；不採用。

## Consequences

- 這是非公開 API，webmail 改版可能打破它；`tests/` 用錄下來的請求形狀做離線測試，
  實機探針另外驗。
- 寫入需要 webmail session；ADR-0003 的自動換 cookie 因此從「查別人行程才需要」變成
  寫入的必要條件。
- 對既有的 CalDAV 會議做任何修改，都會第一次把它送進所有與會者的行事曆並寄信。
