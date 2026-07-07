# m2k 行事曆工具組 — 使用說明

三個交付物，共用同一套已驗證的 m2k（Mail2000）行為分析。挑符合情境的用。

## 檔案一覽

| 檔案 | 用途 | 認證 | 狀態 |
|---|---|---|---|
| `m2k-group-book.user.js` | **webmail 使用者腳本**：群組展開 + 批次加入與會者 | 沿用瀏覽器登入（同源、免 CORS、免 token） | ✅ 已對真實頁面實測 |
| `m2kcal.py` | 行事曆 CLI：查詢 / book / 加與會者 | CalDAV Basic（需應用程式密碼） | ✅ 語法 + 離線測試通過 |
| `m2kgroup.py` | 群組展開 CLI | 需帶瀏覽器 session token（SAML 限制） | ✅ 語法 + 離線測試通過 |
| `test_m2k.py` | 離線單元測試（假資料、免帳密） | — | ✅ 17 項全過 |
| `m2k-calendar-cli-feasibility.md` | 完整技術分析報告 | — | — |

---

## 一、使用者腳本（建議先用這個，大家都能用）

**解決什麼**：book 群組/組織信箱時，底下成員收不到。此腳本把群組**當下成員展開，批次灌進原生「會議排程」的與會者欄**，用 Mail2000 原生流程送出（含「寄送通知信」），成員才會真的收到。

**安裝**
1. Chrome 安裝 Tampermonkey 擴充。
2. Tampermonkey → 新增腳本 → 貼上 `m2k-group-book.user.js` 內容 → 存檔。

> 新版 Chrome 需額外授權：`chrome://extensions` → Tampermonkey → 詳細資料 → 開「**允許使用者指令碼**」，再重整頁面（這是腳本沒出現最常見的原因）。

**使用（v0.4：面板一站式，全部在排程頁完成）**
1. 進 Mail2000 →「會議排程」頁，右下「👥 群組排會議」開面板。
2. **A. 填會議資訊**：標題、日期、開始/結束時間、地點。
3. **B. 加入與會者**（下拉切換）：
   - **搜姓名（autocomplete）**：邊打邊跳建議（中文 1 字、英文 2 字即觸發），點一下該人就加入，右側顯示「＋加入 / ✓已加」。
   - **搜部門**：打部門代碼（如 `CSBDBG`）→ 按「展開並加入」→ **自動抓齊該部門全部成員**逐一加入。
   - 或展開「貼上 email」批次加入。
   - 面板即時顯示「目前與會者：N 人」。
4. **C. 按「✅ 建立會議」**：自動填入原生表單、勾「寄送通知信」、跳出確認後儲存。

**重要：群組信箱 vs 展開**
- 直接打**群組信箱**（如 `cs_csd_csbdbg@gss.com.tw`）只是「一個收件者」,**不會展開**、底下成員收不到 —— 這是原本的問題。
- 要展開成員，請用 **B → 搜部門**（如 CSBDBG），它會列出該部門並把成員一個個加入，大家才收得到。

**原理（皆已實測）**
- 搜人：`/cgi-bin/adb2search_mds`(`command=mdssearch`)，需帶通訊錄範圍（自動取得）。
- 搜部門：`/cgi-bin/adb2tree_mds`(`command=expand`,需帶 `workingabid`) 取部門路徑（如 `/GSS_EMP/CSBDBG`），再用 `/cgi-bin/adb2main_mds`(`pageno` 分頁) 抓齊成員。
- 填表：標題/地點=文字欄；日期=jQuery datepicker `setDate`；時間=`HH:MM` 下拉；儲存鈕 `publishSettingOK`（實測填入正常）。
- 全程同源、只靠瀏覽器登入 cookie、免 token、免 CORS。
- 註：多層部門目前抓「該部門直接成員」;若成員在更下層子部門，改搜那個子部門代碼即可。

**限制：純郵件群組（distribution list）無法展開**
像 `cs_pd3_csbdbg@gss.com.tw` 這種只有「一個群組信箱」的郵件群組，成員名單存在郵件伺服器端，通訊錄與任何前端 API 都讀不到 —— 因此**無法自動拆成個別成員**（連原生 webmail 也看不到其成員）。能展開的只有「通訊錄裡看得到成員」的部門（GSS_EMP 底下 16 個）與個人群組。若要邀請郵件群組的個別成員，需先向群組管理者/IT 取得名單，再用「貼上 email」批次加入。

**已驗證**：對真實排程表單實測——批次加入成員成功、忙碌狀態正常帶出、可正確移除；腳本不會自動存檔，一切以你按「儲存」為準。

---

## 二、行事曆 CLI（m2kcal.py）

```bash
pip install caldav icalendar
export M2K_USER="wilber_chen@gss.com.tw"
export M2K_PASS="應用程式專用密碼"     # 因 SAML，需在信箱設定產生 App 密碼
python3 m2kcal.py agenda --days 7
python3 m2kcal.py book --title "週會" --start "2026-07-10 14:00" --end "2026-07-10 15:00" \
    --attendee user_a@example.com --attendee user_b@example.com
```

> 注意：CalDAV 無排程功能，`--attendee` 只寫入事件、不會自動寄邀請。要通知請用使用者腳本的原生流程。

## 三、群組展開 CLI（m2kgroup.py）

因 SAML SSO，獨立程式無法自行登入，需從瀏覽器帶入 session：

```bash
pip install requests
export M2K_M="<通訊錄網址的 m 參數>"; export M2K_SSNID="<ssnid>"; export M2K_COOKIE="<Cookie>"
python3 m2kgroup.py expand --abid <ABID> --dirid <DIRID> --as-attendees
```

---

## 功能測試怎麼做

1. **離線單元測試**（最快、CI 友善）：`python3 test_m2k.py` → 驗證 ICS 產生、時間解析、成員解析。
2. **即時煙霧測試**：使用者腳本在「會議排程」頁展開一個小群組，看與會者是否正確加入（不要按儲存即無副作用）。
3. **真實整合測試**：用測試行事曆 book 一筆給自己，確認收得到；再小範圍找一位同事驗證群組通知。

## 關鍵限制（務必知道）
- **登入是 SAML SSO** → 獨立 CLI 無法自動登入通訊錄；群組功能最穩的是使用者腳本（沿用瀏覽器登入）。
- **CalDAV 無排程** → 與會者不會由 CalDAV 自動通知；通知走原生流程（使用者腳本）或 .ics 邀請信。
- **「按接受」無法省略** → 那是行事曆邀請的正常確認；腳本的價值在確保每個人都「收到」。
- **CORS** → 純網頁跨站打 mail.gss.com.tw 會被擋；使用者腳本同源運作故無此問題。
