# Mail2000 新版 webmail GraphQL 對行事曆的支援調查

調查日期：2026-09-21
調查對象：`https://mail.gss.com.tw/8/`（新版 Angular webmail）的 `/8/api/graphql`
方法：在已登入分頁內以同源 `fetch(credentials:"include")` 發送唯讀 query，並靜態掃描前端 bundle、實地觀察舊版行事曆的網路請求。全程未發送任何 mutation。

## 結論

**新版 webmail 的 GraphQL 完全沒有行事曆能力，這條路走不通。**
`/8/api/graphql` 的 schema 只涵蓋郵件、資料夾、搜尋、簽名檔、過濾規則、OTP、帳號設定等信箱功能；32 個行事曆相關欄位名稱逐一探測全部回 `Cannot query field ... on type "Query"`，前端 108 個 JS chunk（2.8 MB）中出現的 120 個 GraphQL operation 沒有任何一個與行事曆有關。新版 UI 工具列上的行事曆圖示是一個**外部連結**（`fxExternal href="/8/m2k/calender"`，注意原始碼中的拼字），會直接跳離 SPA 導向舊版 `/cgi-bin/cal/cal_main`，新版前端根本沒有行事曆路由。

更關鍵的是：舊版行事曆的「會議排程」畫面在加入與會者時，實際打的就是本專案目前使用的 `/cgi-bin/cal/calsrv/schedule/{email}/instances`，一位與會者一支請求。**原廠自己也是這樣做的**，沒有隱藏的多人 free-busy 批次 API。建議維持現有 REST 實作。

## Introspection 是否可用

不可用。對 `/8/api/graphql` POST 標準 introspection query，單一物件與 JSON 陣列（批次）兩種 body 格式都回 HTTP 400：

```
GraphQL introspection is not allowed by Apollo Server, but the query contained __schema or __type.
To enable introspection, pass introspection: true to ApolloServer in production
extensions.validationErrorCode = "INTROSPECTION_DISABLED"
```

後端是 Apollo Server，且以 production 模式部署。`__type(name:"Query")` 同樣被擋（同一個驗證規則涵蓋 `__schema` 與 `__type`）。

欄位建議（did-you-mean suggestions）也已關閉，錯誤訊息只有 `Cannot query field "X" on type "Query".`，無法從錯誤訊息反推欄位清單。不過這仍是一個可靠的**存在性 oracle**：GraphQL 驗證階段會一次回報查詢中**所有**無效欄位，因此可以把大量候選欄位放進同一個 query，用回傳的 errors 反推哪些存在。

傳輸層特性（已實測）：
- 支援 JSON 陣列批次，一次送兩個 operation 會回傳兩筆結果（HTTP 200）。
- 前端設定為 `BatchHttpLink`，`uri:"/8/api/graphql"`、`batchMax: Infinity`、`withCredentials: true`（見 `main-*.js`）。
- 前端每支請求都帶 `x-no-touch-session: 1`，本次調查一併沿用。

## 行事曆相關 Query／型別清單

**沒有任何一個。**

以下 32 個候選欄位名稱一次送出，32 個全部回 `Cannot query field ... on type "Query"`：

```
calendar, calendars, calendarList, calendarEvents, calEvents, events, eventList,
schedule, schedules, scheduleList, scheduleInstances, instances, freebusy, freeBusy,
busyTimes, availability, attendees, meeting, meetings, meetingRoom, invitations,
appointments, agenda, calConfig, calendarConfig, calendarEnabled, sharedCalendars,
calendarShare, userSchedule, groupSchedule, eventInstances, calendarModuleEnabled
```

從 `main-*.js` 出發遞迴爬完整個 chunk 依賴圖（108 個檔案、2,827,487 bytes），抽出全部 120 個 GraphQL operation，分類如下，無一與行事曆相關：

| 領域 | 代表 operation |
| --- | --- |
| 郵件讀取 | `mailList`, `mailContent`, `mailMeta`, `folderList`, `folderMeta`, `folderNewMailCount` |
| 郵件操作 | `moveMails`, `deleteMails`, `archiveMails`, `setMailsFlag`, `purgeFolderTrash` |
| 搜尋 | `searchResult`, `searchSuggest`, `searchModuleEnabled`, `vfolderList`, `vfolderSearchId` |
| 過濾規則 | `filterList`, `getFilter`, `addFilter`, `modifyFilter`, `moveFilter` |
| 簽名檔 | `signatureMetas`, `getSignature`, `mailSignature`, `defaultSignatureSetting` |
| 帳號安全 | `otpOptions`, `otpAppDevice`, `advancedAuthInfo`, `clientDevices`, `passwordPolicy` |
| 自動處理 | `autoReplyInfo`, `autoForwardInfo`, `backupEmail`, `delegatableAccounts` |
| 偏好設定 | `preferenceGetString/Number/Boolean`, `m2kConfig`, `feature`, `privilege` |
| 其他 | `serverInfo`, `userQuota`, `reclaimRecord`, `imInfo` |

兩個容易誤判的名稱要特別澄清：

- `scheduledMailList` / `deleteScheduledMail` 是**定時寄信**，不是行事曆排程。
- chunk 中確實含有 ical.js 函式庫（有 `VEVENT`、`freebusy`、`icalendar` 等字串），但那是用來解析郵件中的 `.ics` 附件，並非行事曆 API，也沒有任何 GraphQL operation 使用它。

## 行事曆頁與排程頁的實際請求

新版 UI 的 `/8/s/zh-Hant/calendar/`、`/8/s/zh-Hant/cal/`、`/8/s/zh-Hant/schedule/` 全部回傳同一份 Angular SPA 殼（catch-all 路由），標題仍是「Mail2000 電子信箱」，並非真的存在行事曆路由。真正的入口 `/8/m2k/calender` 會 302 導向 `https://mail.gss.com.tw/cgi-bin/cal/cal_main?`。

舊版行事曆（`cal_main`）載入後共 127 支 XHR，**全部是 REST，零 GraphQL**：

| 用途 | 端點 |
| --- | --- |
| 待辦事項 | `/cgi-bin/cal/calsrv/feeds/default/default/1/todos/` |
| 偏好與系統設定 | `/cgi-bin/cal/calsrv/api/default/preference`、`/api/default/system/getSystemConfig` |
| 行事曆清單 | `/cgi-bin/cal/calsrv/feeds/default/{default,subscribed,public}/` |
| 權限 | `/cgi-bin/cal/calsrv/api/default/acl/{default,subscribed,public}/` |
| 提醒 | `/cgi-bin/cal/calsrv/feeds/default/public/{id}@<網域>/events/alarm/?starttime=&endtime=` |
| 事件展開 | `/cgi-bin/cal/calsrv/feeds/default/default/1/events/instances/?starttime=&endtime=` |

值得注意：光是載入首頁，提醒查詢就對每一本公用行事曆各發一支請求，一口氣打出**超過 110 支** `events/alarm/`。原廠 UI 本身就是 N+1 扇出模式。

切到「會議排程」頁，新增一支：

```
GET /cgi-bin/cal/calsrv/schedule/default/records?starttime=&endtime=
```

在「與會者」欄位輸入一位同事 email 後（表單未儲存，事後以取消捨棄），依序打出兩支：

```
GET /cgi-bin/cal/calsrv/schedule/{email}/privacy
GET /cgi-bin/cal/calsrv/schedule/{email}/instances?starttime=&endtime=
```

`privacy` 是一支輕量前置檢查，回傳 `{"rspCode":0,"rspMsg":"","privacy":<int>}`，用來決定該名與會者的行程要以何種細緻度顯示（畫面上另有「顯示狀態」勾選項對應）。

**這就是決定性證據**：原廠自己的會議排程功能，查每位與會者的行程用的正是本專案目前使用的 `schedule/{email}/instances`，且一人一支、無批次。

## 與現有 REST 排程端點的比較

現有做法：`/cgi-bin/cal/calsrv/schedule/{email}/instances?starttime=&endtime=`，一人一支，回傳 instances 含 `attendee_reply_status`。

| 面向 | GraphQL `/8/api/graphql` | 現有 REST |
| --- | --- | --- |
| 有無行事曆 schema | 沒有 | 有，且是原廠行事曆唯一資料來源 |
| 一次查多人 | 不適用 | 不支援，需自行併發 |
| 欄位裁剪 | 傳輸層支援，但無行事曆型別可裁 | 不支援，回傳固定欄位 |
| 批次請求 | 支援陣列批次、`batchMax: Infinity` | 不支援 |
| 分頁 | 不適用 | 無，以時間區間為界 |
| 錯誤碼 | Apollo 標準 `extensions.code`，比較好判讀 | `rspCode` / `rspMsg`，各端點不一致 |
| introspection | 關閉，無法自我探索 | 無 schema 可探索 |

結論是 GraphQL 在**傳輸層**確實較優（批次、錯誤碼一致），但這些優勢在行事曆上全部落空，因為 schema 裡根本沒有行事曆。目前沒有任何理由為了行事曆去改用 GraphQL。

## 建議

1. **維持現行 REST 實作**。原廠會議排程功能走的就是同一支端點，這是目前唯一且官方認可的取得他人行程路徑，不必擔心用了非預期的內部 API。
2. **考慮納入 `schedule/{email}/privacy` 作為前置檢查**。在打 `instances` 之前先問一次 privacy，可以提早判斷對方是否允許查看、以及應該用什麼細緻度呈現，也讓「查不到」與「無權限」兩種情況可以區分開來，錯誤訊息會更精準。成本是每人多一支輕量請求。
3. **多人查詢就照原廠做法併發**。原廠 UI 自己都在做上百支扇出，一人一支不是誤用。真正該做的是在客戶端控制併發數並加上快取，而不是去找不存在的批次 API。
4. **`schedule/default/records` 值得後續觀察**。這是會議排程頁載入時打的端點，回傳使用者自己已建立的會議排程紀錄，若之後要做「我發起的會議與回覆狀況」類功能，這支比逐一比對 instances 更直接。
5. **GraphQL 仍可用於信箱側功能**。如果未來需要查信件、資料夾、搜尋或帳號設定，`/8/api/graphql` 是完整且現代的選擇，記得帶 `x-no-touch-session: 1` 並善用陣列批次。但行事曆請不要再往這個方向找。

## 調查邊界

- 全程只發送 query 與 introspection，未發送任何 mutation。
- 唯一的 UI 互動是切換分頁與在未儲存的會議表單中填入一個 email 以觀察請求，最後以「取消」捨棄，未建立、修改或刪除任何資料。
- introspection 被關閉，因此「沒有行事曆 schema」是由三條獨立證據交叉支持：32 個候選欄位全數不存在、完整 bundle 掃描的 120 個 operation 無一相關、新版 UI 以外部連結導向舊版行事曆。並非直接讀取 schema 得到。
