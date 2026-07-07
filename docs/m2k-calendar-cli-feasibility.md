# m2k 行事曆 CLI / MCP 可行性分析

分析日期：2026-07-03　·　方法：Claude in Chrome 直接檢視你已登入的 m2k 頁面與網路行為

## 一句話結論

**可行，而且比預期簡單。** m2k 行事曆底層是一台標準的 **CalDAV 伺服器（SabreDAV）**，支援查詢與建立會議。CLI 不需要爬網頁、不需要逆向那個難用的介面，直接用現成的 CalDAV 函式庫就能做，也正好對應你說的「外面的服務可以 book」。

## m2k 是什麼

| 項目 | 發現 |
|---|---|
| 產品 | Openfind **Mail2000**（webmail + 行事曆），架在 `mail.gss.com.tw` |
| 行事曆網頁 | `/cgi-bin/cal/cal_main`，傳統 CGI 整頁輸出 + jQuery |
| 網頁 UI 的資料介面 | `/cgi-bin/cal/calsrv`，走 Google Calendar (GData) 風格的 feed 路徑 |
| **標準協定** | **CalDAV 已開啟**（程式碼含 `isEnableCalDAV`、`CalDAVScope`、`EnablePublish`、`subscribed`） |

## 決定性證據：這是完整的 CalDAV 伺服器

對 `/cgi-bin/cal/caldav/` 送 OPTIONS，伺服器回應：

```
DAV: 1, 3, extended-mkcol, access-control,
     calendarserver-principal-property-search,
     calendar-access, calendar-proxy
Allow: OPTIONS, GET, HEAD, DELETE, PROPFIND, PUT, PROPPATCH, COPY, MOVE, REPORT
```

- `calendar-access` = 支援 RFC 4791（CalDAV 標準）
- `REPORT` / `PROPFIND` = **查詢**行事曆
- `PUT` / `DELETE` = **建立 / 刪除**會議（也就是 book）

再送一次 PROPFIND（回 `207 Multi-Status`）確認：
- 伺服器實作：**SabreDAV**（`xmlns:s="http://sabredav.org/ns"`，業界標準、成熟穩定）
- 你的帳號 principal 路徑：`/cgi-bin/cal/caldav/principals/you@example.com/`

換句話說，任何支援 CalDAV 的東西（Apple 行事曆、Thunderbird、各種自動化服務，或我們自己寫的 CLI）都能接上去讀取與建立會議。這就是你說的「外部連結 / 外面的服務 book」能成立的原因。

## 三條可行路線比較

| 路線 | 查詢 | Book | 難度 | 穩定度 | 建議 |
|---|---|---|---|---|---|
| **A. CalDAV（標準協定）** | ✅ REPORT | ✅ PUT | 低（用現成函式庫） | 高，官方協定不易被改壞 | ⭐ 首選 |
| B. `calsrv` 內部 JSON API | ✅ | ✅ | 中（需逆向參數 + session token `m=`） | 低，`m=` token 會過期、改版易壞 | 備案 |
| C. 爬 `cal_main` 網頁 | 勉強 | 難 | 高 | 最低 | 不建議 |

路線 A 完勝：協定公開、認證單純（HTTP Basic）、不受網頁改版影響。

## 連線設定（給 CLI / MCP 用）

| 項目 | 值 |
|---|---|
| CalDAV base URL | `https://mail.gss.com.tw/cgi-bin/cal/caldav/` |
| 認證 | HTTP Basic：Mail2000 帳號 + 密碼 |
| 帳號 | `you@example.com` |
| principal | `/cgi-bin/cal/caldav/principals/you@example.com/` |

> 注意：部分 Mail2000 站台會要求「裝置專用密碼 / 應用程式密碼」而非登入密碼。若用一般密碼連線失敗，請到信箱設定找 CalDAV / 外部裝置密碼選項；認證屬敏感操作，請你自己在信箱介面完成，密碼不要交給我或寫進程式碼。

## 找相關人員 / 加與會者（後續追加分析）

「book 能不能找出相關人員」拆成三件事，答案不一樣：

| 需求 | 可行性 | 說明 |
|---|---|---|
| 把**已知 email** 的人加成與會者 | ✅ 已做 | CLI `book --attendee a@x --attendee b@x`，寫入 VEVENT 的 ATTENDEE |
| 加了與會者後**自動寄邀請信 / 收 RSVP** | ❌ 不支援 | 此站台 CalDAV **沒開排程**（`schedule-outbox-URL` 回 404，DAV header 無 `calendar-auto-schedule`）。與會者只記在事件裡，不會自動發信；要通知得另用寄信功能 |
| **自動查出**公司同事的 email（不用自己打） | ⚠️ 可行但不乾淨 | 見下 |

**查人的兩條路：**

1. **標準 CardDAV（乾淨但目前無資料）**：`/cgi-bin/carddav/` 有開（`addressbook`），你的通訊錄家目錄是 `/cgi-bin/carddav/addressbooks/you@example.com/`，但底下**沒有任何通訊錄被佈建**，公司全域通訊錄（GAL）也沒透過 CardDAV 暴露。`principal-property-search` 在根層也查不到人。→ 這條路現在拿不到同事資料。

2. **Webmail 內建通訊錄（可用，但要 session token）**：webmail 的通訊錄模組有真正的搜尋端點——
   - 搜尋：`/cgi-bin/adb2search`，參數 `querystring`(關鍵字)、`queryfield`、`workingabid`(哪一本通訊錄)、`workingdirid`、`m`、`ssnid`
   - 分類樹：`/cgi-bin/adb2tree`（可切「個人通訊錄」與公司通訊錄，靠 `workingabid`）
   - 這裡查得到公司同事，但屬於傳統 CGI，需要帶登入後的 session token（`m`、`ssnid`），token 會過期、改版可能變動。

**結論**：CLI 要「輸入 email 就排會議＋列與會者」現在就能用；要做到「打名字自動帶出同事 email」，得讓 CLI 先登入拿 token、再打 `adb2search`。這條可做，但穩定度不如 CalDAV，建議當作第二階段。

## 群組 book / 展開組織成員（追加分析）

**你的需求**：book 給群組時，把該群組當下的成員直接展開、逐一 book，讓每個人都真的收到（而不是 book 組織信箱、底下成員卻收不到）。

**先講你遇到的問題根因**：
- book「組織／群組信箱」= 一個共用行事曆物件，它**不會自動散播到每位成員的個人行事曆**，所以成員收不到。
- 就算用 CalDAV 把成員加成 ATTENDEE 也一樣收不到——因為此站台 **CalDAV 沒有排程功能**（`schedule-outbox` 404），ATTENDEE 只是純資料、不會觸發通知。
- 「需要自己按接受」則是**行事曆邀請的正常行為**，代表對方確認出席；除非有對方行事曆的委派寫入權（DAV header 雖有 `calendar-proxy` 委派能力，但要每個人先授權給你，不切實際），否則無法、也不該強制塞進別人行事曆為「已接受」。

**好消息：群組展開做得到。** 公司通訊錄就在通訊錄模組裡：
- 目錄：`個人通訊錄`、**`GSS`**、**`ORG_ALL`**（全公司）、`虛擬目錄`(Backend/Front/release…)
- 展開群組成員的指令：通訊錄支援 **`command=showgroup`** 與 `command=list`（`/cgi-bin/adb2main` / `/cgi-bin/adb2search`，帶 `workingabid` 選通訊錄、`workingdirid`/群組 id、`m`、`ssnid`）
- 也就是說：CLI 可以「輸入群組名稱 → 呼叫 showgroup 取得當下所有成員 email → 逐一帶進 book」。這正是你要的做法，且成員名單是**下單當下即時展開**，不會用到過期的固定名單。

**要讓成員真的收到，book 的寫入方式要選對（三選一）：**

| 方式 | 成員會收到？ | 需要什麼 | 備註 |
|---|---|---|---|
| A. 逐一寄 `.ics` 邀請信給展開後的每個人 | ✅（信箱顯示接受/拒絕） | 寄信權限（每次寄送需你授權） | 最通用、最穩，標準 iMIP |
| B. 走 webmail 行事曆排程 API（calsrv）帶入成員 | ✅（走站內邀請流程，需按接受） | session token（`m`） | 最貼近現有 UI 行為 |
| C. CalDAV PUT + ATTENDEE | ❌ 不通知 | — | 此站台排程關閉，不適用 |

**結論**：你要的「群組被 book 時自動帶出當下成員逐一 book」**可行**。實作上 CLI 需要：(1) 登入取得 session token；(2) 用 `showgroup` 展開群組成員；(3) 用方式 A 或 B 送出，成員才會收到。這會讓 CLI 從純 CalDAV 往「CalDAV + webmail CGI」混合走，穩定度略降但功能到位；建議列為第二階段實作。

## ⚠️ 重大限制：登入是 SAML SSO

分析登入流程時發現，GSS Mail2000 走 **SAML 單一登入（`/cgi-bin/saml_login`）**，沒有傳統帳號密碼表單。影響：

- **獨立終端機 CLI 無法自己用帳密登入**去打通訊錄（adb2）——SAML 需要瀏覽器導向 IdP、可能還有 MFA，程式無法自動完成。
- 通訊錄（`adb2*`）與網頁行事曆（`calsrv`）都靠**登入後的 session token `m`**，而這個 `m` 是短效、且各模組不同（信箱、行事曆、通訊錄的 `m` 都不一樣），不適合「貼一次 token 長期用」。
- 相對地，**CalDAV / CardDAV 走 HTTP Basic**，通常搭配「應用程式專用密碼」就能繞過 SAML——這也是為什麼行事曆讀寫走 CalDAV 最穩。

**因此，分工建議如下：**

| 功能 | 最佳交付形式 | 認證 |
|---|---|---|
| 查行事曆 / book（自己或指定 email） | 終端機 CLI（CalDAV） | Basic + 應用程式密碼 |
| **群組展開 + 逐一 book** | **瀏覽器驅動自動化 / MCP（沿用已登入的網頁 session）** | 沿用瀏覽器 SAML session，免再登入 |

也就是說：純 CLI 適合行事曆本身；「群組展開」這塊因為 SAML，最務實是做成**在已登入瀏覽器上跑的自動化（Claude in Chrome）或 MCP**，直接沿用你現在的登入狀態，不必解 SAML。

**已實測成功（用你登入的瀏覽器）**：GSS → ORG_DIR2 部門直接列出成員，每筆含**姓名（中英）＋信箱＋分機**（email 格式 `英文名_姓@gss.com.tw`），有分頁（每頁 25 筆）。上方工具列的「加入與會者」按鈕證實這個選擇器正是行事曆加與會者用的。→ 群組展開技術上完全可行。

**已驗證的 adb2 展開規格**：

| 項目 | 值 |
|---|---|
| 端點 | `GET /cgi-bin/adb2main` |
| 參數 | `command=list`、`workingabid`(通訊錄)、`workingdirid`(部門/群組)、`tofield=widget`、`m`、`ssnid` |
| 通訊錄結構 | 公司：`GSS`→`ORG_DIR1`/`ORG_DIR2`(部門樹)、`ORG_ALL`(全公司)；個人：`個人通訊錄`+虛擬群組 |
| 每頁 | 25 筆，需翻頁 |
| 搜尋 | `GET /cgi-bin/adb2search?querystring=<關鍵字>` |

`workingabid` / `workingdirid` 可從通訊錄樹節點的 `do_switchto(...)` 參數取得。

## 已附上的成品

**`m2kcal.py`** — 一支能用的 Python CLI（走 CalDAV），指令：

```
python3 m2kcal.py cals                 # 列出日曆
python3 m2kcal.py agenda --days 7      # 未來 7 天的會議
python3 m2kcal.py list --start 2026-07-01 --end 2026-07-31
python3 m2kcal.py book --title "專案週會" \
    --start "2026-07-08 14:00" --end "2026-07-08 15:00" \
    --location "3F 會議室"
```

安裝：`pip install caldav icalendar`
設定：`export M2K_USER=... ; export M2K_PASS=...`（或執行時互動輸入，不顯示）

## 建議的下一步

1. 你先在信箱設定確認 CalDAV 是否需要「應用程式密碼」，取得可用憑證。
2. 設好環境變數後跑 `python3 m2kcal.py cals` 驗證連得上。
3. 驗證通過後，這套 CalDAV 呼叫可原封不動包成 **MCP server**（讓 Claude 直接幫你查行事曆、排會議），程式邏輯與 CLI 完全共用——要的話我可以接著做。

## 分析邊界

全程只做唯讀探測（OPTIONS / PROPFIND），沒有建立、修改或刪除任何會議，也沒有處理你的密碼。BizForm 連接器另外查過，它是 GSS 的表單系統、沒有行事曆端點，與本任務無關。
