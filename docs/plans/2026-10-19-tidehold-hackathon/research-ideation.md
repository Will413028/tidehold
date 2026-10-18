# PayPal AI Hackathon 選題研究（2026-10-18）

結論：PayPal 官方的 agentic commerce 產品線（Agent Ready、Store Sync、ACP、WebMCP）個人在 sandbox 用不到。實際可用的是 Orders、Invoicing、Subscriptions、Disputes、Payouts、Shipment、Reporting 這幾個 REST API，加上 sandbox MCP server 與 AI Toolkit。這次比賽比的是「真問題 + 能證明的 money movement + 有人把關的 agent 判斷」，不是比誰的 protocol 新。

## 1. PayPal 2025–2026 主推方向與 sandbox 可用性

| 項目 | 現況 | 個人 sandbox 可用？ |
|---|---|---|
| Agent Toolkit / MCP server（`@paypal/mcp`，遠端 `mcp.paypal.com`） | 工具涵蓋 orders、invoices（7 個）、subscriptions（7 個）、disputes（list/get）、catalog、shipment、reporting；commerce 工具 `search_product/create_cart/checkout_cart` 要加 `x-feature-flags: commerce:true` header | 可用。local 版工具最齊，remote 版目前只有 2 個工具（docs 2026-06-16） |
| PayPal AI Toolkit（Claude Code/Codex/Cursor plugin） | 2026-10-16 仍有更新；含 best-practices skill、`/paypal:doctor` 指令、連 sandbox MCP | 可用 |
| Agentic Commerce Services（Agent Ready + Store Sync，2025-10-28 發表） | Agent Ready 透過 Braintree delegated payment token 走 ACP，要「early-access 申請表」 | **不可用**（得是商家，而且要 PayPal 核准） |
| ChatGPT Instant Checkout（ACP）、Google AP2、Perplexity/Copilot | 已和這些平台簽約，Perplexity 與 Copilot 已上線（2026-01） | 不開放開發者 sandbox |
| WebMCP（2026-09-16） | 透過 JS SDK 開放，但只給 Store Sync 商家，要找 Account Manager | **不可用** |
| Invoicing、Subscriptions、Orders v2（authorize/capture/void/refund）、Disputes、Webhooks | 標準 REST API | 可用；sandbox 能模擬 disputes 與 chargeback（`/v2/customer-support/process-chargeback` 僅限 sandbox） |
| Payouts | 正式環境要先申請核准；收款人可用 EMAIL/PHONE/VENMO_HANDLE（Venmo 限 US/USD） | sandbox 一般可直接用（**未驗證**，要到 dashboard 確認 app 已勾選這項功能） |
| Vault、Fastlane、Pay Later、Venmo button | 透過 JS SDK v6 | 大多可用，但與「AI 判斷」關聯弱 |

**評審在講什麼：**
- 官方 build session（2026-10-06）由 Jo、Marco、Eddie 主講：sandbox 設定、AI Toolkit、sandbox MCP、Postman collection、webhooks，以及示範專案的「unhappy paths 測試」（40:35 那一段）。他們重視 sandbox 真實跑通和錯誤路徑。
- Resources 頁的 inspiration 有這幾個點子：「agent 實際成交而不只是推薦」、「工作標記完成就自動付款給承包商」、「agent-to-agent 協商付款」、「approve 後立刻撥款的 gig payout 工具」、「多幣別 freelancer invoicing」。
- Dev blog 最近的文章：2026-10-08 那篇 MCP Hub 講「authorize twice」與「重大動作要人核准」；Eddie 寫過 PayPal + Stripe 雙金流降低流失（約 2026-05）；2026-09-16 那篇談 WebMCP。這些主題和 Will 已有的 mandate gating 方向一致。
- 已公開的 webinar 共 4 場，Discord 討論內容沒查（要登入）。Updates 頁只有 3 則：build session 預告、FAQ 頁、錄影上線。

## 2. 同類比賽得獎者的共同點

- **PayPal Dev Days 2025 hackathon**（2025-05-29）：第 1 名 CrawlerPal（用 PayPal API 做的 paywall AI agent），第 2 名把 Shopware 6 從 JS SDK v5 升到 v6，第 3 名 Rosen（PYUSD 社群交易）。決賽是 shark-tank 式 lightning demo，由 PayPal 主管評審。
- **Square Local Community Hackathon**（2024-06-24）：第 1 名 Wallaby Markets（在地市集攤位預約加自取），同時拿下 best technical API implementation；另有一個獎項給「多個 API 的創意組合」。
- **Google ADK Hackathon**（2025-09-02，477 件作品）：首獎 SalesShortcut（multi-agent SDR，涵蓋開發名單、研究、提案到外聯的完整流程），區域獎多是垂直領域的 multi-agent 流程。
- **MACH AI Exchange Hackathon**（文章 2026-07-09）：首獎 SilentWatch（找出流失客戶並解釋原因）。主辦方明說是「用例清楚勝過 demo 炫技」，強勢作品的特徵是 workflow 而不是 chat、內建人工核准、用 analytics 解釋決策。
- **x402 / Coinbase 系列**（2025）：得獎者多是 agent 付款基礎設施或 POS，Toolkit.dev 是在每次 tool call 收費。PayPal 的評審不太可能偏好這類偏 crypto 基礎設施的題目（推論）。

**共同特徵**：一個具體使用者的完整流程從頭跑到尾；在多個 API 之間串出真實狀態變化；agent 做判斷但留人工核准點，並附證據；demo 用真實 sandbox 資料。**沒查到**：任何一場在 Devpost 上由支付公司贊助、2025–2026 年度的得獎清單（Stripe、Visa、Coinbase Agents in Action 都沒公布名單）。

## 3. 候選問題領域

1. **小型活動或行程業者的團體訂金**（潛水店、嚮導、包船）
   - 誰受害：業者每週都在收訂金，遇到天候、人數不足或個別取消就要退款，客人則抱怨退款規則不透明。
   - 現有方案：FareHarbor、Peek、Rezdy（抽成高，且偏向卡片支付）；PayPal Pool Money。
   - API：Orders v2 AUTHORIZE 預授權、partial capture、void、refund，加 Invoicing 與 webhooks。
   - Agent 判斷：依照業者自己寫的取消政策，加上天氣與成團人數，算出每位客人該 capture、void 還是部分退款，並引用政策條款，由業者核准。
   - Demo：3 個 sandbox buyer 預授權 → 海況不佳 → agent 提出改期或退款方案 → 業者核准 → 真的 void 或 capture。
   - 注意：預授權一般在 3 天內 honor、29 天內有效（依既有知識，**本次未重查**）。
2. **社團、球隊、室友的收款與催繳**
   - 現有方案：Splitwise、Cheddar Up、Venmo、PayPal Pool Money（2024-11 重新推出）。
   - API：Invoicing（send、remind、部分付款）、Orders、Payouts（退還多付的錢）。
   - Agent 判斷：對帳時處理模糊的付款（金額不符、代付）、決定催繳語氣與時機、處理例外。
   - 風險：點子太常見，評審很可能看到一堆分帳 app。
3. **Freelancer 里程碑與逾期款**
   - 現有方案：Upwork escrow、Bonsai、HoneyBook、PayPal 內建 invoice reminder。
   - API：Invoicing（分期、reminder、QR）、Orders authorize。
   - Agent 判斷：對照交付物與 SOW 判斷里程碑是否達成，決定是否收 late fee、寫 change order。
   - Demo：發票 → 部分付款 → 逾期 → agent 建議下一步。
4. **只用 PayPal 的小賣家處理退款與退貨**（社群賣家、獨立站）
   - 誰受害：每天都有「沒收到」「不合」的訊息。
   - 現有方案：Loop Returns、AfterShip、Gorgias（偏 Shopify 生態）。
   - API：Payments refund（含部分退款）、Shipment tracking、Disputes、Reporting。
   - Agent 判斷：讀顧客訊息、訂單與物流狀態，對照退貨政策，決定全額退、部分退、補寄或拒絕，目標是在爭議發生前先化解。
   - Demo：完全可在 sandbox 做到真實退款。
5. **爭議回覆 copilot**
   - 現有方案：Chargeflow（自稱回收率 80–90%，**未驗證**）、Chargehound（PayPal 旗下產品）。
   - API：Disputes（provide-evidence、accept-claim）、sandbox chargeback 模擬。
   - 風險：PayPal 自己已經有同類產品；sandbox 建立爭議需要額外的 header 與步驟。
6. **Gig 或承包商在工作核准後撥款**（resources 頁的原話）
   - 現有方案：Deel、Gusto contractor、Tipalti。
   - API：Payouts（`sender_item_id` 冪等）。
   - Agent 判斷：審查完工證據（照片、工時、清單）是否足以放款，或部分放款。
   - Demo：放款後 sandbox 收款帳戶真的有入帳。
7. **創作者或小型會員制的訂閱流失與催款**
   - 現有方案：Patreon、Memberful、Stripe Smart Retries。
   - API：Subscriptions（暫停、修改方案、取消）、webhooks。
   - Agent 判斷：決定挽留方案（暫停或降級），以及扣款失敗後怎麼催款。
8. **小型組織的報帳**（家長會、社團、NGO 志工）
   - 現有方案：Expensify、Open Collective。
   - API：Payouts。
   - Agent 判斷：讀收據、比對政策、抓重複報帳。

## Top 3

1. **團體行程訂金 agent（#1）**：最能沿用現有後端（AUTHORIZE、void、partial capture、冪等、對帳、mandate gating）。AUTHORIZE 加 void 在 hackathon 作品裡少見，「Technological Implementation」這項有明顯區隔。天候或人數變動讓政策解讀和例外處理一定要靠判斷，規則寫不完。目標受眾具體，demo 全部是真實 sandbox 資金流。有機會角逐 Best Use of PayPal + AI。
2. **PayPal 小賣家退款與退貨分流（#4）**：每天都會發生，痛點清楚。可以直接用 MCP 已有的 refund、shipment、disputes、reporting 工具，評審一眼看得出是「善用 PayPal 平台」，而且能把「預防爭議」量化成 Impact 敘事。
3. **核准後撥款（#6，可與 #3 合併成「freelancer 里程碑撥款」）**：和官方 inspiration 的文字幾乎一樣，代表評審心中確實有這個場景。Payouts 加證據審查的人工核准點，正好對應 PayPal blog 的「重大動作要人核准」，也和 Best Use of Agentic Commerce 有關。風險是 sandbox 的 Payouts 權限還沒驗證。

## Sources
- Hackathon 首頁、resources、updates（2026-10-18 抓取）：https://paypalaihackathon.devpost.com/ 、/resources 、/updates
- Build session 錄影描述（2026-10-06）：https://www.youtube.com/watch?v=FFJfCW9z3Do
- PayPal AI Toolkit（2026-10-16 push）：https://github.com/paypal/AI-Toolkit （`skills/paypal-best-practices/references/{agentic-commerce,mcp-tools,payouts}.md`）
- 注意：resources 頁連到的 docs.paypal.ai 會 301 轉到 developer.paypal.com/docsai/，目前是 404（2026-10-18 用 curl 確認）
- MCP server docs（2026-06-16）：https://developer.paypal.com/limited-release/agents/
- Hackathon 公告（2026-09-30）：https://developer.paypal.com/community/blog/PayPal_AI_Hackathon
- MCP Hub（2026-10-08）：https://developer.paypal.com/community/blog/One_connection_2000_tools
- WebMCP（2026-09-16）：https://developer.paypal.com/community/blog/WebMCP_PayPal_Agent_Ready
- Blog 索引：https://developer.paypal.com/community/blog/
- Agentic Commerce Services（2025-10-28）：https://newsroom.paypal-corp.com/2025-10-28-PayPal-Launches-Agentic-Commerce-Services-to-Power-AI-Driven-Shopping
- Cymbio 收購與 Perplexity/Copilot 上線（2026-01-22）：https://newsroom.paypal-corp.com/2026-01-22-PayPal-to-Acquire-Cymbio,-Accelerating-Agentic-Commerce-Capabilities
- PayPal × OpenAI ACP（2025-10-28）：https://techcrunch.com/2025/10/28/paypal-partners-with-openai-to-let-users-pay-for-their-shopping-within-chatgpt/
- Dev Days 2025 得獎（2025-05-29）：https://developer.paypal.com/community/blog/winners-dev-days-hackathon
- Square Local Community 得獎（2024-06-24）：https://developer.squareup.com/blog/announcing-the-winners-of-the-square-local-community-hackathon/
- ADK hackathon 得獎（2025-09-02）：https://cloud.google.com/blog/products/ai-machine-learning/adk-hackathon-results-winners-and-highlights
- MACH hackathon（2026-07-09）：https://machalliance.org/insights-hub/from-who-showed-up-to-what-they-built
- Coinbase Code NYC：https://www.coinbase.com/developer-platform/discover/launches/code-nyc-recap
- Pool Money 重新推出（2024-11-14）：https://techcrunch.com/2024/11/14/paypals-new-feature-will-let-you-easily-split-expenses-with-family-and-friends
- Disputes sandbox 測試：https://developer.paypal.com/disputes/test-go-live.md
- Chargeflow × PayPal：https://www.chargeflow.io/integrations/paypal
