#!/usr/bin/env python3
"""Minimal storyteller generation pipeline for one PDF -> one HTML output."""

from __future__ import annotations

import html
import json
import markdown
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parent
STORYTELLERS_DIR = PROJECT_DIR / "htmls"
DEFAULT_REWRITE_MODEL = "models/gemini-3.1-flash-lite-preview"
DEFAULT_REWRITE_FALLBACK_CHAIN: List[Dict[str, str]] = [
    {"model": "gemma4:e2b",     "provider": "ollama",     "ollama_base_url": "http://localhost:11434"},
    {"model": "MiniMax-M2.5",   "provider": "minimax.io"},
    {"model": "deepseek-r1:8b", "provider": "ollama",     "ollama_base_url": "http://localhost:11434"},
]
DEFAULT_PDF_EXTRACTION_MODEL = "models/gemini-3.1-flash-lite-preview"
PDF_EXTRACTION_FALLBACK_MODEL = "gemini-2.5-flash"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MINIMAX_PORTAL_BASE_URL = "https://api.minimax.io"
DEFAULT_MAX_SECTIONS = 0
DEFAULT_REWRITE_CHUNK_CHARS = 3000
DEFAULT_REWRITE_MODE = "paragraph"
DEFAULT_APPEND_MISSING_FORMULAS = False
DEFAULT_REWRITE_RESPONSE_FORMAT = "markdown"
DEFAULT_CONCISE_LEVEL = 6
DEFAULT_ANTI_REPEAT_LEVEL = 6
DEFAULT_GEMINI_PREFLIGHT_ENABLED = True
DEFAULT_GEMINI_PREFLIGHT_TIMEOUT_SECONDS = 8
DEFAULT_GEMINI_REWRITE_TIMEOUT_SECONDS = 75
DEFAULT_REWRITE_FALLBACK_TIMEOUT_SECONDS = 300
DEFAULT_POST_REWRITE_AUDIT_ENABLED = True
POST_REWRITE_AUDIT_BATCH_MAX_CHARS = 72000
GEMINI_PREFLIGHT_CACHE_TTL_SECONDS = 120
DEFAULT_STYLE = "storyteller"
LATEX_PLACEHOLDER = "LATEXPH"
GEMINI_EXTRACTION_PROMPT = (
    "請完美提取這份 PDF 的純文字內容，保留原有的章節標題結構（使用 Markdown 標題如 # 或 ##），"
    "並將所有的數學公式完美轉換為標準 LaTeX 語法（使用 $$...$$ 或 $...$ 包裝）。"
    "請直接輸出乾淨的 Markdown 內容，不要包含任何開場白或多餘解釋。"
)
HEADING_HINTS = (
    "abstract",
    "introduction",
    "background",
    "related work",
    "method",
    "methods",
    "approach",
    "experiment",
    "experiments",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
    "references",
    "acknowledgement",
    "acknowledgements",
    "appendix",
)

load_dotenv(dotenv_path=STORYTELLERS_DIR / ".env", override=False)
load_dotenv(dotenv_path=PROJECT_DIR / ".env", override=False)
load_dotenv(dotenv_path=Path.home() / ".env", override=False)

_GEMINI_PREFLIGHT_CACHE: Dict[str, Tuple[float, bool, str]] = {}

# ── 工作簿10對齊：共用機制 ─────────────────────────────────────────────────

_STORYTELLER_EXAMPLE = """
【示範：好的說書人改寫長什麼樣】

原文（假設）：
"We propose a masked loss function that reweights the reconstruction error within the edited region,
encouraging the model to focus on the target area while preserving background content."

❌ 差的改寫（只描述「是什麼」，無類比，無「為什麼」）：
「我們提出一個遮罩損失函數，對編輯區域的重建誤差進行重新加權，讓模型專注在目標區域。」

✅ 好的改寫（有類比、有「為什麼」、有洞察）：
「譬如你在考試卷上畫重點：標記過的地方，你會反覆讀；沒標記的背景文字，你會快速帶過。
VIVA 的遮罩損失函數做的正是這件事——它告訴模型「這個區域是重點，要認真學習；其他背景，你只需要不破壞它就好」。
這個設計解決了一個微妙問題：如果讓模型平等對待所有像素，大片不需要變動的背景會「稀釋」掉應該學習編輯的訊號，導致訓練變慢、改的地方也不精準。
有了遮罩加權，模型的注意力就能精準聚焦在「刀口上」。」

【示範結束——請以上面的好範例為品質標準，改寫你被分配到的節。】
"""

_COMMON_RULES = """
【共通規則 — 不可省略】
1. 改寫內容必須呼應論文原文的核心論點，不得偏離主旨。
2. 嚴禁虛構任何數值、實驗結果或引用文獻；原文有的才能寫，原文沒有的寫「原文未提及」。
"""

STYLE_PROMPTS: Dict[str, str] = {
    "storyteller": """【思考流程】在開始改寫之前，請先在內部思考以下三個問題（不需要輸出這部分）：
① 本節最重要的一個洞察是什麼？（用一句話說）
② 最貼切的生活類比或比喻是什麼？（想一個再動筆）
③ 用哪一句話當開頭最能抓住讀者？
思考完後，直接輸出改寫結果。

說書人（故事化解說 + Why-first + 讀者視角）

【角色與口吻】
- 你是擅長把技術內容寫成**通俗、有趣、故事化**敘事的說書人，目標是讓**無相關領域背景的一般讀者**也能輕鬆讀懂。
- 少用「本文」「該方法」等論文腔；多用「想想看」「我們換個角度說」「為什麼會這樣？」等**引導式口語**，並適度加入趣味與畫面感，但不油滑、不低俗。
- **開場句勿制式重複**：各節請輪替使用不同開場策略（懸念一句、反問、對照、小場景、時間序、直接拋出矛盾等），**避免**多篇連續都以「想像一下」「想想看」或同一套話起首；若章節定位顯示非第 1 節，尤須避免與前序章節開頭句式雷同。

【逐單元改寫結構】
- 以 subsection 為改寫單元（若無則以 section 為單元），不可省略原文任一段落。
- 每單元依序：① 日常情境或懸念引入 → ② 動機與直覺先行 → ③ 解釋「為什麼這樣設計、為何有效」 → ④ 說明此單元的重要性。
- 類比是橋樑：用類比引入後立刻說清楚技術機制，同一類比全文只展開一次。
- 遇到設計取捨時，主動說明「為何選這個而非替代方案、犧牲了什麼換來什麼」。

【文字風格】
- 在不遺漏**關鍵知識概念**、且敘事與論證**文意流暢**的前提下，篇幅可**精煉**，刪減累贅、降低閱讀負擔；避免為拉長而重複同義句。
- **公式與數值／實驗範例必須完整保留**，維持原本 LaTeX 定界符，並**無縫融入**故事化改寫，不可為求精簡而刪略或口號式帶過。
- 長短句交替；重要術語首次出現時白話解釋並加 **粗體**。
- 溫暖度 {warmth}／10、視覺化程度 {visual}／10、數學密度 {math_density}／10、詼諧感 {humor}／1。

【術語與公式對照（本節必附，置於改寫正文之後、自評段落之前）】
- 改寫正文結束後，依序附兩個 Markdown 表格（欄位清楚、單格不宜過長）：
    1. **本節專業術語白話對照表**：僅列本節**新增、新義或本節首次須獨立對照**之術語（含縮寫），建議欄位「術語｜白話說明」。若提示中有【強制禁止重複解釋】所列術語，**禁止**在本表再次完整定義或複製前節長篇解釋（可完全不列，或至多一行註「見前節正文／前節對照表」）；**務必避免與其他節對照表內容重複或換句話說的同義重列**。
    2. **本節公式白話解釋表**：僅針對本節**首次出現或語境／角色有實質新增**之 LaTeX 式給完整白話欄；若某式與前序章節已解釋過的式子**完全相同**（同形 LaTeX），**不得**再貼一大段重複解釋——表中保留該式一行即可，白話欄寫「同式已於前序章節說明，本節僅承接語境」或類似極短銜接說明；若本節無任何須獨立展開之新公式，表中註明並簡述。
- 兩表為文末自評之依據：須覆蓋本節**應首次交代**之術語與公式，同時整體閱讀上**與前節對照表不重複累贅**，不得敷衍。

【自評機制】改寫與兩表完成後，於**全文最末**依序輸出：
1. **與前文重複檢視**（純文字 2-4 句）：若章節定位顯示本節**非**第 1 節，須自查本節是否在**生活類比、故事主線、懸念開場或橋段節奏**上，與前序章節可能之重複或過度近似，並說明是否已於正文中避開；若為第 1 節則寫「（首節無須比對前文情節）」。
2. **對照表檢核**（純文字 1-3 句）：確認兩表已涵蓋本節**應首次交代**之術語與公式，且**未與前序各節對照表重複冗餘列示**（術語不重複定義、同形公式不重複長解），無重大遺漏。
3. 最後一行自評標記（務必原樣保留格式）：
<!-- EVAL: 類比[有/無] | 為什麼[有/無] | 虛構[無/有] | 前文情節或類比重複[無/有] | 術語表檢核[通過/未通過] | 公式白話表檢核[通過/未通過] -->
- 若「類比=無」或「為什麼=無」或「虛構=有」或「前文情節或類比重複=有」或兩表檢核任一為「未通過」，皆須重新修改後再輸出。
""" + _STORYTELLER_EXAMPLE + _COMMON_RULES,

    "blog": """【思考流程】在開始改寫之前，請先在內部思考以下三個問題（不需要輸出）：
① 哪個生活情境或痛點最適合用來引入這個概念？
② 讀者看了會覺得「這和我什麼關係」？
③ 要用哪一句話作為結論收束？
思考完後，直接輸出改寫結果。

科普部落格（知識轉譯 + 讀者關聯 + 易讀不失真）

【角色視角】
- 你是一位擅長把技術內容寫成大眾知識型文章的專業部落客。
- 目標是讓一般讀者願意讀完、看懂重點，並知道這和自己有什麼關係。

【改寫單元規則】
- 以 subsection 為改寫單元（若無 subsection 則以 section 為單元）。
- 若某改寫單元僅包含一個段落，可與前後較合適的單元合併（合併後須完整涵蓋原屬各單元之重點，不得縮減資訊）。
- 不可省略原文任一改寫單元之實質內容。
- 以下五段可依單元內容在**不缺漏重要知識與概念**的前提下**彈性**調整篇幅與順序：
    1. 引入：用一般讀者在意的問題、迷思、現象或痛點切入。
    2. 直覺：先用白話說清楚「它在解什麼問題」。
    3. 原理：再講核心方法或機制。
    4. 重要性：說明「這和讀者有什麼關係」。
    5. 收束：一句本節重點或可行動的理解結論。
- 無論如何編排，整體須具**起、承、轉、合**，並以最適合讀者理解的話語做直白闡釋（可比照說書人風格的口語與說理深度，仍維持部落格文章體例）；**任一公式與範例均不可省略**，須**無縫融入**改寫正文，維持原本 LaTeX 定界符（若有）。

【改寫原則】
- 先講直覺，再講原理，再講重要性（可依單元彈性交錯，但邏輯須清楚）。
- 技術術語第一次出現時，立刻用白話解釋；重要概念首次出現請加 **粗體**。
- 可用類比、比較、情境例子、常見誤解幫助理解。
- 風格：輕鬆、詼諧、自然、清楚、有吸引力，但不浮誇、不聳動。

【公式與技術內容處理】
- **公式與範例均不可省略**，須無縫融入改寫內容，並以淺顯易懂的話語解釋其意涵。
- 不可刪除會影響理解結論的關鍵技術內容。

【輸出格式】
- 產出一個吸引一般讀者的標題。
- 前言 2-4 句：說明為何值得讀。
- 內文分成數個有小標題的段落，每段以 **3–5 句**為主。
- 結語：重點整理 + 一個延伸思考問題。

【本節專業術語白話解釋表（必附，不可省略）】
- **位置**：須在**本節改寫輸出**的末尾（結語之後、自評 EVAL 行之前）；**每一節各自**附一表。**不是**整篇論文合併成 HTML 後的「全文最末」才附表——你現在一次只輸出**單一節**的改寫，該表即為**這一節文字的最後區塊之一**（EVAL 仍為**本節**最末一行）。
- 必須附上一則 Markdown 表格，標題須清楚點出為**本節**專業術語白話解釋（標題用語可合理同義變體，例如「本節專業術語白話對照」）。
- 建議欄位「術語｜白話說明」；僅列本節**新增、新義或本節須獨立對照**之術語（含縮寫），單格不宜過長。若本節新術語極少，仍須保留表格並至少對照本節最關鍵的少量術語，**禁止**整表留空或僅寫「無」敷衍。
- **勿**與前序節對照表做無意義的同義重列；前序節已詳解者可在白話欄極短註「見前節」而非再次長篇定義。

【自評機制】本節改寫完成後，必須在**本節輸出之最末一行**加上自評標記（**緊接在本節術語表之後**；勿推到其他節或全文合併後才寫），格式如下：
<!-- EVAL: 讀者關聯[有/無] | 直覺解釋[有/無] | 輕鬆詼諧[有/無] -->
若任一項為「無」，或未於**本節末尾**附術語白話表，必須重新修改後再輸出。

【風格參數】
- 親和度 {affinity}／10、吸睛度 {hook}／10、技術密度 {tech_density}／10、觀點感 {stance}／10、詼諧感 {humor}／1。
""" + _COMMON_RULES,

    "professor": """【思考流程】在開始改寫之前，請先在內部思考以下三個問題（不需要輸出）：
① 這個概念的嚴謹定義是什麼？
② 哪種講解順序（基礎到進階）最適合教學？
③ 這個機制能對應到什麼具體的考點或重點？
思考完後，直接輸出改寫結果。

大教授（課堂講義 + 條理清楚 + 可複習）

【角色視角】
- 你是一位教學經驗豐富的專業教師，擅長把複雜內容整理成條理清楚、適合教學與複習的講義。
- 目標不是營造聊天感，而是產出可直接用於上課或考前複習的教材內容。

【改寫原則】
- 以學生理解與複習需求為優先。
- 依教學邏輯重組內容：先基礎，再進階；先直觀，再正式；先概念，再細節。
- 每個重要術語第一次出現時，要先給清楚定義與白話解釋。
- 說明「這是什麼、為什麼需要、怎麼運作、有何限制」。
- 為強化學習效果，可依本節內容**補充**（擇要、勿空泛堆砌）：**數值範例之逐步計算演示**、**分點**、**比較**、**常見誤解**、**注意事項**、**重點整理**、**助憶金句**（短句口訣，便於考前回想）。
- 風格要像正式但易懂的課堂講義，而不是故事文或部落格文。

【輸出格式】
- 依序整理為：主題、學習目標、背景意識、定義、原理、實例、比較限制、重點整理。
- 使用小節標題與分層條列，讓讀者容易掃描與複習。
- 重要概念首次出現時加 **粗體**。

【本節專業術語白話解釋表與公式意義白話解釋表（必附，不可省略）】
- **位置**：兩表皆須在**本節改寫輸出**的末尾、且於**本節**自評 EVAL 行**之前**（**每一節各自**附兩表；**不是**整篇 HTML 合併後的全文末尾）。
- **表一「本節專業術語白話解釋」**：Markdown 表，建議欄位「術語｜白話說明」；僅列本節須獨立對照之術語／縮寫；單格不宜過長；**禁止**整表留空或僅「無」敷衍。跨節勿無意義重複前序節長篇定義（可極短註「見前節」）。
- **表二「本節公式意義白話解釋」**：Markdown 表，建議欄位「公式（LaTeX 原文）｜意義與變數白話說明」；針對本節**首次出現或須獨立交代**之 LaTeX 式；若本節無須展開之新公式，表中須註明並簡述本節與式子之銜接關係，**不可**省略此表。
- **輸出順序**：講義正文（含上述八段結構）→ **表一** → **表二** → 最末一行 `<!-- EVAL: ... -->`。

【自評機制】本節改寫完成後，必須在**本節輸出之最末一行**加上自評標記（**緊接兩表之後**），格式如下：
<!-- EVAL: 條理分層[有/無] | 預先定義[有/無] -->
若任一項為「無」，或未於**本節末尾**完成兩表，必須重新修改後再輸出。

【風格參數】
- 正式度 {formality}／10、條理化程度 {structure}／10、初學者友善度 {beginner_friendly}／10、數學密度 {math_density}／10、考點導向 {exam_focus}／10。
""" + _COMMON_RULES,

    "fairy": """【思考流程】在開始改寫之前，請先在內部思考以下四個問題（不需要輸出）：
① 原文的核心概念應該映射成童話中怎樣的角色或道具？
② 運作流程如何轉化為主角遭遇的衝突與解決過程？
③ 原文中的公式或數值，要用「咒語」「石碑刻文」「魔法結界方程」還是其他形式嵌入故事？
④ 故事最後要帶出什麼核心寓意（知識點）？
思考完後，直接輸出改寫結果。

童話故事（知識童話 + 角色化 + 公式融入 + 寓意對應）

【任務目標】
- 保留原文**最核心的概念、邏輯關係與所有公式**，改寫成有角色、有場景、有衝突、有解法、有寓意的童話故事。

【改寫單元規則】
- 以 subsection 為改寫單元（若無 subsection 則以 section 為單元）。
- 若某改寫單元僅包含一個段落，可與前後較合適的單元合併（合併後須完整涵蓋原屬各單元之知識重點，不得縮減內容）。

【改寫原則】
- 將重要概念轉化為角色、道具、地點、力量或規則；故事中角色**第一次登場**時，須隱含或明示其對應的真實技術概念。
- 將技術流程改寫成故事中的任務、困難與解決過程；情節轉折須忠實反映原文的**技術邏輯**，而非單純製造戲劇衝突。
- **保留核心機制**：不可只剩童話氣氛而失去知識內容。
- 優先用故事事件呈現概念，而不是直接講術語。
- **任一原文公式與數值範例均不可省略**：須以「神秘咒語」「石碑刻文」「魔法結界方程」等故事化包裝**無縫嵌入**情節，並在角色使用或解讀該式的過程中說清楚變數意義。
- **開場句勿制式重複**：各節請輪替使用不同開場策略（懸念、旁白、角色對白、場景描寫、反問、時間序等），避免多節連續以相同套語起首；若非第 1 節，尤須與前序節開場策略差異化。

【輸出格式】
- 故事須依序包含以下五段，各段使用清楚的小標題：
    1. **故事標題**
    2. **開場**（場景與主角登場）
    3. **情節發展**（衝突與挑戰，融入公式與核心機制）
    4. **問題如何解決**（技術邏輯的展開與收束）
    5. **寓意與真實知識對應**（需包含：① 一段話明示故事元素↔技術概念映射摘要；② 若有公式，複述其在故事中的「真實身份」，一句話即可）

【本節角色與概念對照表、公式意義白話說明表（必附，不可省略）】
- **位置**：兩表皆須在**本節改寫輸出**的末尾、自評 EVAL 行**之前**；**每一節各自**附兩表。
- **表一「本節角色與概念對照」**：Markdown 表，欄位「故事元素（角色／道具／地點）｜對應真實技術概念」；列出本節**新登場或新賦予意義**的故事元素，單格不宜過長；**禁止**整表留空或僅「無」敷衍；跨節勿無意義重複前序節已交代的映射（可極短註「見前節」）。
- **表二「本節公式意義白話說明」**：Markdown 表，欄位「公式（LaTeX 原文）｜故事中的身份與數學意義白話說明」；針對本節**首次出現或在情節中有實質新角色**之 LaTeX 式；若本節無任何須獨立展開的新公式，表中須註明並簡述本節與公式的銜接關係，**不可**省略此表。
- **輸出順序**：故事正文（含上述五段）→ **表一** → **表二** → 最末一行 `<!-- EVAL: ... -->`。

【自評機制】本節改寫完成後，必須在**本節輸出之最末一行**加上自評標記（**緊接兩表之後**），格式如下：
<!-- EVAL: 角色映射[有/無] | 公式融入[有/無] | 核心知識保留[有/無] | 寓意說明[有/無] | 對照表檢核[通過/未通過] | 虛構[無/有] -->
若任一項為「無」或「有（虛構）」或「未通過」，必須重新修改後再輸出。

【風格參數】
- 童話感 {fairy_tone}／10、知識保真度 {fidelity}／10、年齡定位 {age_level}／10、畫面感 {visual}／10、解說顯性程度 {explicitness}／10。
""" + _COMMON_RULES,

    "lazy": """【思考流程】在開始改寫之前，請先在內部思考以下三件事（不需要輸出）：
① 貫穿本節最核心的一句話結論是什麼？
② 要列出哪幾個最關鍵的 bullet points 才能涵蓋所有重點？
③ 原文中哪些公式或數值是理解核心機制不可或缺的？要怎樣以最精簡的一行融入條列？
思考完後，直接輸出改寫結果。

懶人包（結論先行 + 條列整理 + 公式速查 + 快速吸收）

【角色視角】
- 你是一位擅長把複雜內容濃縮成高可讀懶人包的知識編輯。
- 優先追求可快速掃描與快速理解，但不以犧牲公式與關鍵數值為代價。

【改寫單元規則】
- 以 subsection 為改寫單元（若無 subsection 則以 section 為單元）。
- 若某改寫單元僅包含一個段落，可與前後較合適的單元合併（合併後須完整涵蓋各單元重點，不得縮減資訊）。

【改寫原則】
- 先講結論，再補背景與細節。
- 用條列、短段、對照與重點框架幫助快速吸收。
- 技術術語第一次出現時，給一句最短可懂的白話解釋；重要概念首次出現加 **粗體**。
- 不可為了精簡而刪掉會影響理解的關鍵限制、前提或結果。
- **任一原文公式與數值範例均不可省略**；若有 LaTeX 式，須以 `> 公式：$...$` 區塊或嵌入對應條列點的方式呈現，並用括號或短句說明式子意涵。
- 跨節勿無意義重複已在前節解釋過的術語（可極短註「見前節」）。

【輸出格式】
- 依序整理為：
    1. **一句話結論**
    2. **背景／問題**
    3. **重點條列**（約 {bullet_count} 點，每點 1-2 句）
    4. **（選用）公式速查**：若本節有須理解的 LaTeX 式，集中列於此處，每式一行，格式：`$式$ ← 一句意涵`；若本節無公式可省略此塊。
    5. **限制或注意事項**
    6. **一句帶走重點**
- 不要寫成長篇散文。

【本節術語速查表與公式速查對照表（必附，不可省略）】
- **位置**：兩表皆須在**本節改寫輸出**的末尾、EVAL 行**之前**；**每一節各自**附兩表。
- **表一「本節術語速查」**：Markdown 表，欄位「術語｜一句白話」；僅列本節**新增或須獨立對照**之術語（含縮寫）；**禁止**整表留空或僅「無」敷衍；跨節已詳解者可極短註「見前節」。
- **表二「本節公式速查」**：Markdown 表，欄位「公式（LaTeX 原文）｜意涵一句話」；針對本節**首次出現或語境有新角色**之 LaTeX 式；若本節無任何須獨立展開的新公式，表中須標明並簡述本節與公式的銜接關係，**不可**省略此表。
- **輸出順序**：六段正文 → **表一** → **表二** → 最末一行 `<!-- EVAL: ... -->`。

【自評機制】本節改寫完成後，必須在**本節輸出之最末一行**加上自評標記（**緊接兩表之後**），格式如下：
<!-- EVAL: 結論先行[有/無] | 條列摘要[有/無] | 公式保留[有/無] | 速查表檢核[通過/未通過] | 虛構[無/有] -->
若任一項為「無」或「未通過」或「有（虛構）」，必須重新修改後再輸出。

【風格參數】
- 條列點數 {bullet_count}、濃縮度 {compression}／10、初學者友善度 {beginner_friendly}／10、圖像化程度 {visual}／10、重點力度 {takeaway_strength}／10。
""" + _COMMON_RULES,

    "question": """【思考流程】在開始改寫之前，請先在內部思考以下問題（不需要輸出）：
① 依照原文邏輯，我應該設計哪幾個由淺入深的循序提問？
② 如何在回答最後一個問題時，剛好收束本節核心重點？
思考完後，直接輸出改寫結果。

問題驅動（提問引導 + 逐層拆解 + 收束答案）

【任務目標】
- 以提問與回答逐層建立理解。

【改寫原則】
- 先問讀者真正會在意的問題，再回答。
- 問題應能對應原文中的關鍵動機、方法、限制或結果。
- 回答順序以「問題是什麼 → 為什麼困難 → 作者怎麼解 → 有什麼限制或結果」為主。

【輸出格式】
- 核心提問（約 {question_count} 題）
- 逐題回答（包含背景、方法拆解、限制與結果）
- 最後總結

【自評機制】改寫完成後，必須在輸出結尾加上一行自評標記，格式如下：
<!-- EVAL: 提問引導[有/無] | 解惑完整度[高/低] -->
若為「無」或「低」，必須重新修改後再輸出。

【風格參數】
- 問題數量 {question_count}、好奇心強度 {curiosity}／10、拆解深度 {depth}／10、初學者友善度 {beginner_friendly}／10、收束力度 {closure_strength}／10。
""" + _COMMON_RULES,

    "log": """【思考流程】在開始改寫之前，請先在內部思考（不需要輸出）：
① 這裡觀察到了什麼技術問題或工程挑戰？
② 進行了哪些設計取捨（Trade-offs）？
③ 調整後的具體結果是什麼？
思考完後，直接輸出改寫結果。

實驗日誌（研究過程記錄、工程師視角）

【任務目標】
- 用工程師觀點客觀描述研究流程、觀察與決策取捨，有如一份系統開發除錯紀錄。

【改寫原則】
- 強調「發現問題 → 方案取捨 → 進行調整 → 得到結果」。
- 語氣必須客觀、專業、實事求是，像可追蹤的研發紀錄。
- 主動說明「放棄了哪些次佳方案」以及「為什麼採用目前的設計」。
- 重視細節與證據，若原文提及數據或特徵，應明確記錄。

【輸出格式】
- 以 {log_count} 篇日誌或觀察報告的形式產出。
- 標示如 [Observation] (觀察)、[Decision] (決策取捨)、[Result] (結果) 等明確段落。

【自評機制】改寫完成後，必須在輸出結尾加上一行自評標記，格式如下：
<!-- EVAL: 問題觀察[有/無] | 取捨分析[有/無] | 客觀度[符合/不符合] -->
若有任何「無」或「不符合」，必須重新修改後再輸出。

【風格參數】
- 日誌數量 {log_count}、客觀度 {objectivity}／10、細節度 {detail_level}／10、取捨聚焦度 {tradeoff_focus}／10。
""" + _COMMON_RULES,
}

# Per-style adjustable parameters with defaults.
# Each entry: {key, label, min, max, step, default}
STYLE_PARAMS: Dict[str, List[Dict[str, Any]]] = {
    "storyteller": [
        {"key": "warmth",       "label": "溫暖度",     "min": 0, "max": 10, "step": 1,   "default": 7},
        {"key": "visual",       "label": "視覺化程度", "min": 0, "max": 10, "step": 1,   "default": 8},
        {"key": "math_density", "label": "數學密度",   "min": 0, "max": 10, "step": 1,   "default": 4},
        {"key": "humor",        "label": "詼諧感",     "min": 0, "max": 1,  "step": 0.1, "default": 0.5},
    ],
    "blog": [
        {"key": "affinity",     "label": "親和度",   "min": 0, "max": 10, "step": 1,   "default": 7},
        {"key": "hook",         "label": "吸睛度",   "min": 0, "max": 10, "step": 1,   "default": 8},
        {"key": "tech_density", "label": "技術密度", "min": 0, "max": 10, "step": 1,   "default": 4},
        {"key": "stance",       "label": "觀點感",   "min": 0, "max": 10, "step": 1,   "default": 6},
        {"key": "humor",        "label": "詼諧感",   "min": 0, "max": 1,  "step": 0.1, "default": 0.4},
    ],
    "professor": [
        {"key": "formality",         "label": "正式度",       "min": 0, "max": 10, "step": 1, "default": 7},
        {"key": "structure",         "label": "條理化程度",   "min": 0, "max": 10, "step": 1, "default": 9},
        {"key": "beginner_friendly", "label": "初學者友善度", "min": 0, "max": 10, "step": 1, "default": 8},
        {"key": "math_density",      "label": "數學密度",     "min": 0, "max": 10, "step": 1, "default": 4},
        {"key": "exam_focus",        "label": "考點導向",     "min": 0, "max": 10, "step": 1, "default": 6},
    ],
    "fairy": [
        {"key": "fairy_tone",  "label": "童話感",       "min": 0, "max": 10, "step": 1, "default": 8},
        {"key": "fidelity",    "label": "知識保真度",   "min": 0, "max": 10, "step": 1, "default": 8},
        {"key": "age_level",   "label": "年齡定位",     "min": 0, "max": 10, "step": 1, "default": 7},
        {"key": "visual",      "label": "畫面感",       "min": 0, "max": 10, "step": 1, "default": 9},
        {"key": "explicitness", "label": "解說顯性程度", "min": 0, "max": 10, "step": 1, "default": 6},
    ],
    "lazy": [
        {"key": "bullet_count",      "label": "條列點數量",   "min": 3, "max": 8,  "step": 1, "default": 5},
        {"key": "compression",       "label": "濃縮度",       "min": 0, "max": 10, "step": 1, "default": 8},
        {"key": "beginner_friendly", "label": "初學者友善度", "min": 0, "max": 10, "step": 1, "default": 7},
        {"key": "visual",            "label": "圖像化程度",   "min": 0, "max": 10, "step": 1, "default": 6},
        {"key": "takeaway_strength", "label": "重點力度",     "min": 0, "max": 10, "step": 1, "default": 8},
    ],
    "question": [
        {"key": "question_count",    "label": "問題數量",     "min": 2, "max": 5,  "step": 1, "default": 3},
        {"key": "curiosity",         "label": "好奇心強度",   "min": 0, "max": 10, "step": 1, "default": 8},
        {"key": "depth",             "label": "拆解深度",     "min": 0, "max": 10, "step": 1, "default": 7},
        {"key": "beginner_friendly", "label": "初學者友善度", "min": 0, "max": 10, "step": 1, "default": 8},
        {"key": "closure_strength",  "label": "收束力度",     "min": 0, "max": 10, "step": 1, "default": 7},
    ],
    "log": [
        {"key": "log_count",         "label": "日誌數量",     "min": 1, "max": 5,  "step": 1, "default": 3},
        {"key": "objectivity",       "label": "客觀度",       "min": 0, "max": 10, "step": 1, "default": 8},
        {"key": "detail_level",      "label": "細節度",       "min": 0, "max": 10, "step": 1, "default": 7},
        {"key": "tradeoff_focus",    "label": "取捨聚焦度",   "min": 0, "max": 10, "step": 1, "default": 6},
    ]
}


def _get_style_prompt(style_key: str, params: Dict[str, Any] = None) -> str:
    """Return the style prompt with parameters substituted (falls back to defaults)."""
    template = STYLE_PROMPTS.get(style_key, STYLE_PROMPTS[DEFAULT_STYLE])
    # Build substitution dict: start from defaults, overlay with supplied params
    param_defs = STYLE_PARAMS.get(style_key, [])
    values: Dict[str, Any] = {p["key"]: p["default"] for p in param_defs}
    if params:
        for k, v in params.items():
            if k in values:
                values[k] = v
    if not values:
        return template
    try:
        return template.format_map(values)
    except (KeyError, ValueError):
        return template


def run_storyteller_pipeline(job: Dict[str, Any], *, phase_reporter=None) -> Dict[str, Any]:
    """Run one minimal storyteller generation job end-to-end.

    Args:
        job: Job dict from the job store.
        phase_reporter: Optional callable(label: str) -> None.  Called at each
            major pipeline phase so the caller can persist progress (e.g. write a
            ``phase`` field back to the job store for frontend polling).

    Payload (optional):
        post_rewrite_audit_enabled: bool, default True. When True and style is
            ``storyteller``, ``blog``, or ``professor`` (including ``podcast`` alias),
            runs one LLM batch audit after all sections and replaces or rewrites
            sections that fail that style's audit checklist.
    """
    payload = job.get("payload", {}) if isinstance(job, dict) else {}
    if not isinstance(payload, dict):
        payload = {}

    # ── Manual sections mode: bypass PDF extraction entirely ──────────────────
    manual_sections_raw = payload.get("manual_sections")
    if isinstance(manual_sections_raw, list) and manual_sections_raw:
        if phase_reporter:
            phase_reporter("使用手動輸入改寫單元…")
        sections = []
        for item in manual_sections_raw:
            if isinstance(item, dict):
                title = str(item.get("title") or "未命名單元").strip() or "未命名單元"
                body = str(item.get("body") or "").strip()
                if body:
                    sections.append({"title": title, "source_text": body})
        if not sections:
            raise RuntimeError("Manual sections provided but all bodies were empty.")
        paper_title_slug = _slugify(str(payload.get("paper_title", "manual_input")).strip() or "manual_input")
        pdf_path = Path(f"{paper_title_slug}.pdf")  # virtual path used only for naming
        extracted_text = ""
        extraction_warning = None
        pdf_extraction_model = "manual"
    else:
        # ── Normal PDF extraction path ─────────────────────────────────────────
        pdf_path = _resolve_pdf_path(job=job, payload=payload)
        if pdf_path is None:
            raise ValueError(
                "No readable PDF path found in job payload. "
                "Supported keys include pdf_path/source_pdf_path/input_path/file_path/path/pdf."
            )

        if phase_reporter:
            phase_reporter("PDF 文字掃描中…")
        extracted_text, extraction_warning, pdf_extraction_model = _extract_pdf_text(pdf_path)
        if not extracted_text.strip():
            raise RuntimeError(f"No text extracted from PDF: {pdf_path}")

        if phase_reporter:
            phase_reporter("段落結構解析中…")
        sections = _split_into_sections(extracted_text)
        if not sections:
            raise RuntimeError(f"Unable to build sections from extracted text: {pdf_path}")

    max_sections = _safe_positive_int(payload.get("max_sections"), DEFAULT_MAX_SECTIONS)
    rewrite_chunk_chars = _safe_positive_int(
        payload.get("rewrite_chunk_chars"),
        DEFAULT_REWRITE_CHUNK_CHARS,
    )
    rewrite_mode = _normalize_rewrite_mode(payload.get("rewrite_mode"))
    rewrite_response_format = _normalize_rewrite_response_format(
        payload.get("rewrite_response_format")
    )
    concise_level = _safe_range_int(
        payload.get("concise_level"),
        default=DEFAULT_CONCISE_LEVEL,
        min_value=0,
        max_value=10,
    )
    anti_repeat_level = _safe_range_int(
        payload.get("anti_repeat_level"),
        default=DEFAULT_ANTI_REPEAT_LEVEL,
        min_value=0,
        max_value=10,
    )
    gemini_preflight_enabled = _normalize_bool(
        payload.get("gemini_preflight_enabled"),
        DEFAULT_GEMINI_PREFLIGHT_ENABLED,
    )
    gemini_preflight_timeout_seconds = _safe_range_int(
        payload.get("gemini_preflight_timeout_seconds"),
        default=DEFAULT_GEMINI_PREFLIGHT_TIMEOUT_SECONDS,
        min_value=3,
        max_value=30,
    )
    gemini_rewrite_timeout_seconds = _safe_range_int(
        payload.get("gemini_rewrite_timeout_seconds"),
        default=DEFAULT_GEMINI_REWRITE_TIMEOUT_SECONDS,
        min_value=15,
        max_value=240,
    )
    rewrite_fallback_timeout_seconds = _safe_range_int(
        payload.get("rewrite_fallback_timeout_seconds"),
        default=DEFAULT_REWRITE_FALLBACK_TIMEOUT_SECONDS,
        min_value=10,
        max_value=180,
    )
    append_missing_formulas = _normalize_bool(
        payload.get("append_missing_formulas"),
        DEFAULT_APPEND_MISSING_FORMULAS,
    )
    primary_model = str(payload.get("model") or DEFAULT_REWRITE_MODEL)
    fallback_chain_raw = payload.get("rewrite_fallback_chain")
    if isinstance(fallback_chain_raw, list) and fallback_chain_raw:
        fallback_chain: List[Dict[str, str]] = [
            {k: str(v) for k, v in spec.items() if isinstance(v, (str, int, float))}
            for spec in fallback_chain_raw if isinstance(spec, dict)
        ]
    else:
        fallback_chain = [dict(spec) for spec in DEFAULT_REWRITE_FALLBACK_CHAIN]
    # Derive legacy single-fallback fields for backward-compat summary/return values.
    _first_fallback = fallback_chain[0] if fallback_chain else {}
    fallback_model: str = _first_fallback.get("model", "")
    fallback_provider: str = _first_fallback.get("provider", "")
    ollama_base_url = str(
        payload.get("ollama_base_url")
        or os.getenv("OLLAMA_BASE_URL")
        or os.getenv("OLLAMA_HOST")
        or DEFAULT_OLLAMA_BASE_URL
    ).rstrip("/")
    minimax_base_url = str(
        payload.get("minimax_base_url") or os.getenv("MINIMAX_PORTAL_BASE_URL") or DEFAULT_MINIMAX_PORTAL_BASE_URL
    ).rstrip("/")
    minimax_oauth_token = str(
        payload.get("minimax_oauth_token")
        or payload.get("minimax_token")
        or os.getenv("MINIMAX_PORTAL_OAUTH_TOKEN")
        or os.getenv("MINIMAX_OAUTH_TOKEN")
        or ""
    ).strip()
    style = _normalize_style(payload.get("style"))

    style_params: Dict[str, Any] = {}
    raw_style_params = payload.get("style_params")
    if isinstance(raw_style_params, dict):
        style_params = raw_style_params

    rendered_sections: List[Dict[str, Any]] = []
    llm_failures: List[str] = []
    rewrite_models_used: set[str] = set()
    if extraction_warning:
        llm_failures.append(extraction_warning)

    selected_sections = sections
    skipped_sections = 0
    if max_sections > 0:
        selected_sections = sections[:max_sections]
        skipped_sections = max(0, len(sections) - len(selected_sections))
        if skipped_sections > 0:
            llm_failures.append(
                f"Skipped {skipped_sections} sections because max_sections={max_sections}."
            )

    rewrite_chunks_generated = 0
    total_sections = len(selected_sections)
    introduced_concepts: List[str] = []  # accumulates term names across sections
    for index, section in enumerate(selected_sections, start=1):
        if phase_reporter:
            phase_reporter(f"說書改寫中（第 {index}／{total_sections} 節）…")
        rewrite_parts = _split_section_into_rewrite_parts(
            section_title=section["title"],
            source_text=section["source_text"],
            max_chunk_chars=rewrite_chunk_chars,
            rewrite_mode=rewrite_mode,
        )
        rewrite_chunks_generated += len(rewrite_parts)

        section_story_parts: List[str] = []
        section_terms: List[Dict[str, str]] = []
        section_formula_explanations: List[Dict[str, str]] = []
        section_used_llm = False
        section_used_models: set[str] = set()

        for part_index, part in enumerate(rewrite_parts, start=1):
            _rewrite_kwargs = dict(
                section_title=part["title"],
                source_text=part["source_text"],
                model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                style=style,
                rewrite_response_format=rewrite_response_format,
                append_missing_formulas=append_missing_formulas,
                style_params=style_params,
                concise_level=concise_level,
                anti_repeat_level=anti_repeat_level,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=gemini_rewrite_timeout_seconds,
                fallback_timeout_seconds=rewrite_fallback_timeout_seconds,
                section_index=index,
                section_count=total_sections,
                introduced_concepts=introduced_concepts if introduced_concepts else None,
            )
            rewritten_text, terms, formula_explanations, used_llm, failure, used_model = _rewrite_section(
                **_rewrite_kwargs
            )
            if failure:
                # Retry once after a brief pause for transient errors (e.g. Gemini 504, Ollama overload)
                time.sleep(5)
                rt2, te2, fe2, ul2, fa2, um2 = _rewrite_section(**_rewrite_kwargs)
                if not fa2:
                    rewritten_text, terms, formula_explanations, used_llm, failure, used_model = (
                        rt2, te2, fe2, ul2, fa2, um2
                    )
                else:
                    failure = f"兩次均失敗 — 第一次: {failure}; 第二次: {fa2}"
                    llm_failures.append(
                        f"section {index} part {part_index}/{len(rewrite_parts)}: {failure}"
                    )
            if used_model:
                section_used_models.add(used_model)
                rewrite_models_used.add(used_model)
            if used_llm:
                section_used_llm = True
            if rewritten_text.strip():
                section_story_parts.append(rewritten_text.strip())
            if terms:
                section_terms.extend(terms)
                # Accumulate term names for cross-section context
                for t in terms:
                    term_name = t.get("term", "").strip()
                    if term_name and term_name not in introduced_concepts:
                        introduced_concepts.append(term_name)
            if formula_explanations:
                section_formula_explanations.extend(formula_explanations)

        section_story_text = "\n\n".join(section_story_parts).strip()
        if not section_story_text:
            section_story_text = section["source_text"]

        rendered_sections.append(
            {
                "index": index,
                "title": section["title"],
                "source_text": section["source_text"],
                "story_text": section_story_text,
                "terms": section_terms,
                "formula_explanations": section_formula_explanations,
                "used_llm": section_used_llm,
                "used_model": ", ".join(sorted(section_used_models)),
                "rewrite_chunks": len(rewrite_parts),
            }
        )

    post_rewrite_audit_enabled = _normalize_bool(
        payload.get("post_rewrite_audit_enabled"),
        DEFAULT_POST_REWRITE_AUDIT_ENABLED,
    )
    post_rewrite_audit_executed = False
    if post_rewrite_audit_enabled and rendered_sections:
        if style == "storyteller":
            post_rewrite_audit_executed = True
            if phase_reporter:
                phase_reporter("說書改寫總稽核與修正…")
            _post_rewrite_storyteller_audit(
                rendered_sections,
                primary_model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                rewrite_response_format=rewrite_response_format,
                append_missing_formulas=append_missing_formulas,
                style_params=style_params,
                concise_level=concise_level,
                anti_repeat_level=anti_repeat_level,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=gemini_rewrite_timeout_seconds,
                fallback_timeout_seconds=rewrite_fallback_timeout_seconds,
                llm_failures=llm_failures,
            )
        elif style == "blog":
            post_rewrite_audit_executed = True
            if phase_reporter:
                phase_reporter("部落格改寫總稽核與修正…")
            _post_rewrite_blog_audit(
                rendered_sections,
                primary_model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                rewrite_response_format=rewrite_response_format,
                append_missing_formulas=append_missing_formulas,
                style_params=style_params,
                concise_level=concise_level,
                anti_repeat_level=anti_repeat_level,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=gemini_rewrite_timeout_seconds,
                fallback_timeout_seconds=rewrite_fallback_timeout_seconds,
                llm_failures=llm_failures,
            )
        elif style == "professor":
            post_rewrite_audit_executed = True
            if phase_reporter:
                phase_reporter("講義體改寫總稽核與修正…")
            _post_rewrite_professor_audit(
                rendered_sections,
                primary_model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                rewrite_response_format=rewrite_response_format,
                append_missing_formulas=append_missing_formulas,
                style_params=style_params,
                concise_level=concise_level,
                anti_repeat_level=anti_repeat_level,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=gemini_rewrite_timeout_seconds,
                fallback_timeout_seconds=rewrite_fallback_timeout_seconds,
                llm_failures=llm_failures,
            )
        elif style == "fairy":
            post_rewrite_audit_executed = True
            if phase_reporter:
                phase_reporter("知識童話改寫總稽核與修正…")
            _post_rewrite_fairy_audit(
                rendered_sections,
                primary_model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                rewrite_response_format=rewrite_response_format,
                append_missing_formulas=append_missing_formulas,
                style_params=style_params,
                concise_level=concise_level,
                anti_repeat_level=anti_repeat_level,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=gemini_rewrite_timeout_seconds,
                fallback_timeout_seconds=rewrite_fallback_timeout_seconds,
                llm_failures=llm_failures,
            )
        elif style == "lazy":
            post_rewrite_audit_executed = True
            if phase_reporter:
                phase_reporter("懶人包改寫總稽核與修正…")
            _post_rewrite_lazy_audit(
                rendered_sections,
                primary_model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                rewrite_response_format=rewrite_response_format,
                append_missing_formulas=append_missing_formulas,
                style_params=style_params,
                concise_level=concise_level,
                anti_repeat_level=anti_repeat_level,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=gemini_rewrite_timeout_seconds,
                fallback_timeout_seconds=rewrite_fallback_timeout_seconds,
                llm_failures=llm_failures,
            )

    rewrite_model = primary_model
    fallback_models_used = rewrite_models_used - {primary_model}
    if fallback_models_used and primary_model in rewrite_models_used:
        rewrite_model = f"{primary_model} (partial fallback: {', '.join(sorted(fallback_models_used))})"
    elif fallback_models_used and primary_model not in rewrite_models_used:
        rewrite_model = ", ".join(sorted(fallback_models_used))

    if phase_reporter:
        phase_reporter("輸出 HTML…")
    title = _resolve_title(payload=payload, pdf_path=pdf_path, sections=rendered_sections)
    output_path = _build_output_path(pdf_path=pdf_path, payload=payload, title=title, job=job)
    output_html = _build_story_html_document(
        title=title,
        pdf_path=pdf_path,
        rendered_sections=rendered_sections,
        model=rewrite_model,
        style=style,
    )

    STORYTELLERS_DIR.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output_html, encoding="utf-8")

    return {
        "pipeline": "storyteller_hybrid_generation",
        "implemented": True,
        "job_id": job.get("job_id"),
        "input": payload,
        "pdf_path": str(pdf_path),
        "output_path": str(output_path),
        "model": rewrite_model,
        "rewrite_model_primary": primary_model,
        "rewrite_model_fallback": fallback_model,
        "rewrite_model_fallback_provider": fallback_provider,
        "pdf_extraction_model": pdf_extraction_model,
        "style": style,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections_detected": len(sections),
        "sections_processed": len(selected_sections),
        "sections_skipped_by_limit": skipped_sections,
        "sections_generated": len(rendered_sections),
        "rewrite_chunks_generated": rewrite_chunks_generated,
        "rewrite_chunk_chars": rewrite_chunk_chars,
        "rewrite_mode": rewrite_mode,
        "rewrite_response_format": rewrite_response_format,
        "concise_level": concise_level,
        "anti_repeat_level": anti_repeat_level,
        "gemini_preflight_enabled": gemini_preflight_enabled,
        "gemini_preflight_timeout_seconds": gemini_preflight_timeout_seconds,
        "gemini_rewrite_timeout_seconds": gemini_rewrite_timeout_seconds,
        "rewrite_fallback_timeout_seconds": rewrite_fallback_timeout_seconds,
        "append_missing_formulas": append_missing_formulas,
        "max_sections": max_sections,
        "post_rewrite_audit_enabled": post_rewrite_audit_enabled,
        "post_rewrite_audit_executed": post_rewrite_audit_executed,
        "steps": [
            {"name": "ingest_source", "status": "done", "note": str(pdf_path)},
            {
                "name": "pdf_to_structured_content",
                "status": "done",
                "note": (
                    f"{len(sections)} detected / {len(selected_sections)} processed"
                    f" / {skipped_sections} skipped by max_sections"
                ),
            },
            *(
                [
                    {
                        "name": "post_rewrite_storyteller_audit",
                        "status": "done",
                        "note": "LLM audit and targeted fixes for storyteller sections",
                    }
                ]
                if post_rewrite_audit_executed and style == "storyteller"
                else [
                    {
                        "name": "post_rewrite_blog_audit",
                        "status": "done",
                        "note": "LLM audit and targeted fixes for blog-style sections",
                    }
                ]
                if post_rewrite_audit_executed and style == "blog"
                else [
                    {
                        "name": "post_rewrite_professor_audit",
                        "status": "done",
                        "note": "LLM audit and targeted fixes for professor-style sections",
                    }
                ]
                if post_rewrite_audit_executed and style == "professor"
                else []
            ),
            {
                "name": "html_story_render",
                "status": "done",
                "note": str(output_path),
            },
        ],
        "artifacts": [
            {
                "type": "html",
                "style": style,
                "path": str(output_path),
                "filename": output_path.name,
            }
        ],
        "warnings": llm_failures,
    }


def _resolve_pdf_path(job: Dict[str, Any], payload: Dict[str, Any]) -> Optional[Path]:
    candidate_values: List[str] = []

    # Most common payload keys first.
    for key in (
        "pdf_path",
        "source_pdf_path",
        "input_pdf_path",
        "input_path",
        "file_path",
        "path",
        "pdf",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            candidate_values.append(value.strip())

    source = payload.get("source")
    if isinstance(source, dict):
        for key in ("pdf_path", "path", "file_path"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                candidate_values.append(value.strip())

    # Fall back to scanning job-level keys if needed.
    for key in (
        "pdf_path",
        "source_pdf_path",
        "input_path",
        "file_path",
        "path",
        "pdf",
    ):
        value = job.get(key)
        if isinstance(value, str) and value.strip():
            candidate_values.append(value.strip())

    seen: set[str] = set()
    for candidate in candidate_values:
        for path in _candidate_pdf_paths(candidate):
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            if path.exists() and path.is_file() and path.suffix.lower() == ".pdf":
                return path.resolve()
    return None


def _candidate_pdf_paths(raw_value: str) -> List[Path]:
    value = raw_value.strip()
    if not value:
        return []

    base = Path(value).expanduser()
    paths = [base]
    if not base.is_absolute():
        paths.append(Path.cwd() / base)
        paths.append(STORYTELLERS_DIR / base)
    return paths


def _extract_pdf_text(pdf_path: Path) -> Tuple[str, Optional[str], str]:
    gemini_api_key = _configure_gemini()
    if not gemini_api_key:
        return (
            _extract_pdf_text_with_pymupdf(pdf_path),
            "No Gemini API Key found; fell back to PyMuPDF.",
            "pymupdf (fallback)",
        )

    from gemini_client import (
        delete_file_quiet,
        generate_content_text_from_client,
        make_client,
        upload_file,
        wait_for_uploaded_file_active,
    )

    sample_file = None
    extraction_models = [DEFAULT_PDF_EXTRACTION_MODEL, PDF_EXTRACTION_FALLBACK_MODEL]
    model_errors: List[str] = []
    client = make_client(gemini_api_key, timeout=600)
    try:
        sample_file = upload_file(client, pdf_path)
        wait_for_uploaded_file_active(client, sample_file.name, timeout_seconds=90)

        for model_name in extraction_models:
            try:
                markdown_text = generate_content_text_from_client(
                    client,
                    model=model_name,
                    contents=[sample_file, GEMINI_EXTRACTION_PROMPT],
                )
            except Exception as exc:
                model_errors.append(f"{model_name}: {type(exc).__name__}: {exc}")
                continue

            if markdown_text:
                warning = None
                if model_name != DEFAULT_PDF_EXTRACTION_MODEL:
                    warning = (
                        "Primary extraction model failed; "
                        f"used fallback {model_name}."
                    )
                return _normalize_extracted_text(markdown_text), warning, model_name
            model_errors.append(f"{model_name}: empty extraction content")
    except Exception as e:
        model_errors.append(f"upload/process: {type(e).__name__}: {e}")
    finally:
        if sample_file is not None:
            delete_file_quiet(client, sample_file.name)

    detail = "; ".join(model_errors) if model_errors else "unknown reason"
    return (
        _extract_pdf_text_with_pymupdf(pdf_path),
        f"Gemini extraction failed ({detail}); fell back to PyMuPDF.",
        "pymupdf (fallback)",
    )


def _configure_gemini() -> str:
    # Random order among GOOGLE/GEMINI/GEMINI_* candidates; first passing list_models wins.
    from gemini_keys import pick_working_gemini_api_key

    return pick_working_gemini_api_key()


def _extract_pdf_text_with_pymupdf(pdf_path: Path) -> str:
    try:
        import fitz  # PyMuPDF
    except Exception as exc:
        raise RuntimeError("PyMuPDF (fitz) is required for PDF extraction") from exc

    try:
        document = fitz.open(str(pdf_path))
    except Exception as exc:
        raise RuntimeError(f"Failed to open PDF with PyMuPDF: {pdf_path}") from exc

    try:
        lines = _extract_structured_pdf_lines(document)
    finally:
        document.close()

    if not lines:
        return ""

    structured_text = _compose_text_from_pdf_lines(lines)
    return _normalize_extracted_text(structured_text)


def _extract_structured_pdf_lines(document: Any) -> List[Dict[str, Any]]:
    line_items: List[Dict[str, Any]] = []
    for page_index, page in enumerate(document):
        page_dict = page.get_text("dict")
        blocks = [block for block in page_dict.get("blocks", []) if block.get("type") == 0]
        blocks.sort(key=lambda block: _bbox_sort_key(block.get("bbox")))

        for block in blocks:
            for line in block.get("lines", []):
                spans = [span for span in line.get("spans", []) if str(span.get("text", "")).strip()]
                if not spans:
                    continue
                text = _join_pdf_spans(spans)
                if not text:
                    continue

                size_values = [float(span.get("size") or 0.0) for span in spans]
                max_size = max(size_values) if size_values else 0.0
                is_bold = any(
                    _is_bold_font_name(str(span.get("font", ""))) or (int(span.get("flags", 0)) & 16)
                    for span in spans
                )

                line_bbox = line.get("bbox") or spans[0].get("bbox") or [0.0, 0.0, 0.0, 0.0]
                x0 = float(line_bbox[0]) if len(line_bbox) > 0 else 0.0
                y0 = float(line_bbox[1]) if len(line_bbox) > 1 else 0.0

                line_items.append(
                    {
                        "page": page_index,
                        "x0": x0,
                        "y0": y0,
                        "text": text,
                        "font_size": max_size,
                        "is_bold": bool(is_bold),
                    }
                )

    line_items.sort(key=lambda item: (item["page"], item["y0"], item["x0"]))
    body_font = _estimate_body_font_size(line_items)
    for item in line_items:
        item["is_heading_hint"] = _is_structural_heading_line(
            text=item["text"],
            font_size=float(item.get("font_size") or 0.0),
            is_bold=bool(item.get("is_bold")),
            body_font_size=body_font,
        )
    return line_items


def _compose_text_from_pdf_lines(lines: List[Dict[str, Any]]) -> str:
    if not lines:
        return ""

    sections: List[str] = []
    current_paragraph = ""
    previous_line: Optional[Dict[str, Any]] = None

    def _flush_paragraph() -> None:
        nonlocal current_paragraph
        paragraph = current_paragraph.strip()
        if paragraph:
            sections.append(paragraph)
        current_paragraph = ""

    for line in lines:
        text = str(line.get("text", "")).strip()
        if not text or _is_simple_page_artifact_line(text):
            previous_line = line
            continue

        if bool(line.get("is_heading_hint")):
            _flush_paragraph()
            sections.append(_normalize_heading_text(text))
            previous_line = line
            continue

        start_new_paragraph = False
        if previous_line is None:
            start_new_paragraph = True
        elif line["page"] != previous_line["page"]:
            start_new_paragraph = True
        elif _is_list_item_start(text):
            start_new_paragraph = True
        else:
            vertical_gap = float(line["y0"]) - float(previous_line.get("y0", line["y0"]))
            previous_text = str(previous_line.get("text", "")).strip()
            if vertical_gap > 18:
                start_new_paragraph = True
            elif previous_text and re.search(r"[.?!:;。？！：；]$", previous_text):
                if vertical_gap > 10 and not re.match(r"^[a-z0-9(\[\"'“‘]", text):
                    start_new_paragraph = True

        if start_new_paragraph:
            _flush_paragraph()
            current_paragraph = text
        else:
            current_paragraph = _merge_text_fragments(current_paragraph, text)

        previous_line = line

    _flush_paragraph()
    return "\n\n".join(section for section in sections if section.strip())


def _bbox_sort_key(bbox: Any) -> Tuple[float, float]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 2:
        return (0.0, 0.0)
    try:
        return (float(bbox[1]), float(bbox[0]))
    except (TypeError, ValueError):
        return (0.0, 0.0)


def _join_pdf_spans(spans: List[Dict[str, Any]]) -> str:
    text = "".join(str(span.get("text", "")) for span in spans)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _is_bold_font_name(font_name: str) -> bool:
    lowered = font_name.lower()
    return any(token in lowered for token in ("bold", "black", "heavy", "demi"))


def _estimate_body_font_size(lines: List[Dict[str, Any]]) -> float:
    if not lines:
        return 11.0

    candidates: List[float] = []
    for line in lines:
        text = str(line.get("text", "")).strip()
        size = float(line.get("font_size") or 0.0)
        if size <= 0:
            continue
        if len(text) < 25:
            continue
        if _looks_like_heading(text):
            continue
        candidates.append(size)

    if not candidates:
        candidates = [float(line.get("font_size") or 0.0) for line in lines if float(line.get("font_size") or 0.0) > 0]
    if not candidates:
        return 11.0
    return float(median(candidates))


def _is_structural_heading_line(*, text: str, font_size: float, is_bold: bool, body_font_size: float) -> bool:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned or len(cleaned) > 160:
        return False

    if _looks_like_heading(cleaned):
        return True

    if re.search(r"[.?!。？！]$", cleaned):
        return False

    words = re.findall(r"[A-Za-z][A-Za-z0-9'/-]*|[\u4e00-\u9fff]+|\d+(?:\.\d+)*", cleaned)
    if not words or len(words) > 16:
        return False

    size_ratio = font_size / max(body_font_size, 1.0)
    if size_ratio >= 1.18:
        return True
    if is_bold and size_ratio >= 1.05 and len(words) <= 12:
        return True
    return False


def _normalize_extracted_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n\n")
    lines = [line.rstrip() for line in normalized.splitlines()]
    cleaned_lines: List[str] = []
    for line in lines:
        striped = line.strip()
        if not striped:
            cleaned_lines.append("")
            continue
        if re.fullmatch(r"\d{1,4}", striped):
            continue
        if re.fullmatch(r"(?i)page\s+\d+(\s+of\s+\d+)?", striped):
            continue
        cleaned_lines.append(striped)

    cleaned_lines = _drop_repeated_page_artifacts(cleaned_lines)
    compact = "\n".join(cleaned_lines)
    compact = re.sub(r"\n{3,}", "\n\n", compact)
    return compact.strip()


def _drop_repeated_page_artifacts(lines: List[str]) -> List[str]:
    if not lines:
        return []

    counts: Dict[str, int] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        key = _artifact_signature(stripped)
        if key:
            counts[key] = counts.get(key, 0) + 1

    repeated = {key for key, count in counts.items() if count >= 3}
    if not repeated:
        return list(lines)

    filtered: List[str] = []
    for line in lines:
        stripped = line.strip()
        key = _artifact_signature(stripped) if stripped else None
        if key and key in repeated:
            continue
        filtered.append(line)
    return filtered


def _artifact_signature(line: str) -> Optional[str]:
    text = re.sub(r"\s+", " ", line).strip()
    if not text or len(text) > 90:
        return None
    if len(text.split()) > 12:
        return None
    if re.search(r"[.?!。？！]$", text):
        return None
    if re.fullmatch(r"[\W_]+", text):
        return None
    if _looks_like_heading(text):
        return None

    lowered = text.lower()
    has_metadata_term = bool(
        re.search(
            r"\b(arxiv|preprint|proceedings|conference|journal|copyright|doi|accepted|manuscript)\b",
            lowered,
        )
    )
    if not has_metadata_term and not _is_mostly_title_or_upper(text):
        return None

    normalized = re.sub(r"\d+", "0", lowered)
    normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if len(normalized) < 4:
        return None
    return normalized


def _is_mostly_title_or_upper(text: str) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z0-9'/-]*", text)
    if not words:
        return False
    if text == text.upper():
        return True
    titled = sum(1 for word in words if word[0].isupper())
    return titled / len(words) >= 0.7


def _split_into_sections(extracted_text: str) -> List[Dict[str, str]]:
    blocks = _split_blocks(extracted_text)
    if not blocks:
        return []

    sections: List[Dict[str, str]] = []
    current_title = "Overview"
    current_paragraphs: List[str] = []

    for block in blocks:
        inline_heading = _split_inline_heading_block(block)
        if inline_heading is not None:
            heading, body = inline_heading
            if current_paragraphs:
                _append_or_merge_section(
                    sections=sections,
                    title=current_title,
                    source_text="\n\n".join(current_paragraphs).strip(),
                )
                current_paragraphs = []
            if _same_heading(heading, current_title) and not body:
                continue
            current_title = heading
            if body:
                current_paragraphs.append(body)
            continue

        if _looks_like_heading(block):
            normalized_heading = _normalize_heading_text(block)
            if current_paragraphs:
                _append_or_merge_section(
                    sections=sections,
                    title=current_title,
                    source_text="\n\n".join(current_paragraphs).strip(),
                )
                current_paragraphs = []
            if _same_heading(normalized_heading, current_title):
                continue
            current_title = normalized_heading
            continue
        current_paragraphs.append(block)

    if current_paragraphs:
        _append_or_merge_section(
            sections=sections,
            title=current_title,
            source_text="\n\n".join(current_paragraphs).strip(),
        )

    if not sections:
        return [{"title": "Content", "source_text": "\n\n".join(blocks)}]
    return sections


def _append_or_merge_section(*, sections: List[Dict[str, str]], title: str, source_text: str) -> None:
    text = source_text.strip()
    if not text:
        return
    if sections and _same_heading(sections[-1].get("title", ""), title):
        previous = str(sections[-1].get("source_text", "")).strip()
        sections[-1]["source_text"] = f"{previous}\n\n{text}".strip() if previous else text
        return
    sections.append({"title": title, "source_text": text})


def _same_heading(left: str, right: str) -> bool:
    left_key = re.sub(r"\s+", " ", str(left or "")).strip().casefold()
    right_key = re.sub(r"\s+", " ", str(right or "")).strip().casefold()
    return bool(left_key and right_key and left_key == right_key)


def _split_inline_heading_block(block: str) -> Optional[Tuple[str, str]]:
    text = re.sub(r"\s+", " ", block).strip()
    if not text:
        return None

    hints_pattern = "|".join(re.escape(hint) for hint in HEADING_HINTS)
    match = re.match(
        rf"^(?P<head>{hints_pattern})\s*(?:[-–—:]\s+|\s{{2,}})(?P<body>.+)$",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        heading = _normalize_heading_text(match.group("head"))
        body = match.group("body").strip()
        if len(body) >= 30:
            return heading, body

    abstract_inline = re.match(r"^(abstract)\s+(?P<body>.+)$", text, flags=re.IGNORECASE)
    if abstract_inline:
        body = abstract_inline.group("body").strip()
        if len(body) >= 40 and re.search(r"[.?!。？！]", body):
            return "Abstract", body

    return None


def _normalize_heading_text(text: str) -> str:
    heading = re.sub(r"\s+", " ", text).strip(" \t-–—:")
    heading = re.sub(r"^#{1,6}\s+", "", heading).strip()
    heading = re.sub(r"\s+#+\s*$", "", heading).strip()
    if not heading:
        return heading
    if heading.isupper() or heading.islower():
        return heading.title()
    return heading


def _split_blocks(extracted_text: str) -> List[str]:
    lines = _drop_repeated_page_artifacts(extracted_text.splitlines())
    normalized_text = "\n".join(lines)
    blocks: List[str] = []
    for raw_block in re.split(r"\n\s*\n+", normalized_text):
        lines = [line.strip() for line in raw_block.splitlines() if line.strip()]
        if not lines:
            continue
        lines = [line for line in lines if not _is_simple_page_artifact_line(line)]
        if not lines:
            continue
        merged = _merge_wrapped_lines(lines)
        if merged:
            blocks.append(merged)
    return _merge_block_continuations(blocks)


def _is_simple_page_artifact_line(line: str) -> bool:
    text = line.strip()
    if not text:
        return True
    if re.fullmatch(r"\d{1,4}\s*[/\-]\s*\d{1,4}", text):
        return True
    if re.fullmatch(r"[-–—]?\s*\d{1,4}\s*[-–—]?", text):
        return True
    if re.fullmatch(r"(?i)page\s+\d+(\s+of\s+\d+)?", text):
        return True
    return False


def _merge_block_continuations(blocks: List[str]) -> List[str]:
    if not blocks:
        return []

    merged_blocks: List[str] = [blocks[0]]
    for block in blocks[1:]:
        previous = merged_blocks[-1]
        if _should_merge_blocks(previous, block):
            merged_blocks[-1] = _merge_text_fragments(previous, block)
        else:
            merged_blocks.append(block)
    return merged_blocks


def _should_merge_blocks(previous: str, current: str) -> bool:
    left = previous.strip()
    right = current.strip()
    if not left or not right:
        return False
    if _looks_like_heading(right):
        return False
    if re.match(r"(?i)^(figure|fig\.|table)\s+\d+[:.]", right):
        return False
    if left.endswith("-"):
        return True
    if re.search(r"[.?!:;。？！：；]$", left):
        return False
    if re.match(r"^[a-z0-9(\[\"'“‘]", right):
        return True
    if re.match(r"(?i)^(and|or|but|because|which|that|where|when|with|for|to|of|in|on|by|as)\b", right):
        return True
    return False


def _merge_wrapped_lines(lines: List[str]) -> str:
    if not lines:
        return ""
    merged_lines: List[str] = [lines[0].strip()]
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if _is_list_item_start(stripped):
            merged_lines.append(stripped)
        else:
            merged_lines[-1] = _merge_text_fragments(merged_lines[-1], stripped)
    return "\n".join(merged_lines).strip()


def _is_list_item_start(line: str) -> bool:
    return bool(re.match(r"^([\-*•]\s+|\d{1,2}[.)]\s+)", line))


def _merge_text_fragments(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    if left.endswith("-") and re.match(r"^[A-Za-z\u4e00-\u9fff]", right):
        return left[:-1] + right
    if right[0] in ",.;:!?)]}%":
        return left + right
    return left + " " + right


def _looks_like_heading(block: str) -> bool:
    text = re.sub(r"\s+", " ", block).strip()
    if not text or len(text) > 160:
        return False
    if re.match(r"^#{1,6}\s+.+$", text):
        return True
    if re.search(r"[.?!。？！]$", text):
        return False
    if re.match(r"(?i)^https?://", text):
        return False
    if re.match(r"(?i)^(figure|fig\.|table)\s+\d+[:.]", text):
        return False
    if re.match(r"^\(?\d+(\.\d+){0,4}\)?[.)]?\s+[A-Za-z0-9\u4e00-\u9fff]", text):
        return True
    if re.match(r"(?i)^[ivxlcdm]{1,8}(?:-[A-Z])?[.)]?\s+[A-Za-z0-9\u4e00-\u9fff]", text):
        return True
    if re.match(r"(?i)^appendix\s+[a-z0-9ivxlcdm]+([.:)\s]|$)", text):
        return True
    if re.match(r"(?i)^(section|chapter|part)\s+[0-9a-zivxlcdm]+(?:\.\d+)*([.:)\s]|$)", text):
        return True

    lowered = text.lower().rstrip(":")
    if any(
        lowered == hint or lowered.startswith(f"{hint} ") or lowered.startswith(f"{hint}:")
        for hint in HEADING_HINTS
    ):
        return True

    words = re.findall(r"[A-Za-z][A-Za-z0-9'/-]*|[\u4e00-\u9fff]+|\d+(?:\.\d+)*", text)
    if not words or len(words) > 14:
        return False

    alpha_words = [word for word in words if re.search(r"[A-Za-z\u4e00-\u9fff]", word)]
    if not alpha_words:
        return False
    if text == text.upper() and len(words) <= 12:
        return True

    titled = sum(1 for word in alpha_words if _is_title_like_word(word))
    lowercase = sum(1 for word in alpha_words if word[:1].islower())
    if titled >= max(2, int(len(alpha_words) * 0.6)) and lowercase <= max(1, len(alpha_words) // 3):
        return True
    return False


def _is_title_like_word(word: str) -> bool:
    if re.search(r"[\u4e00-\u9fff]", word):
        return True
    if word.isupper():
        return True
    return word[:1].isupper()


def _rewrite_section(
    *,
    section_title: str,
    source_text: str,
    model: str,
    fallback_chain: List[Dict[str, str]],
    ollama_base_url: str,
    minimax_base_url: str,
    minimax_oauth_token: str,
    style: str,
    rewrite_response_format: str,
    append_missing_formulas: bool,
    style_params: Dict[str, Any] = None,
    concise_level: int = DEFAULT_CONCISE_LEVEL,
    anti_repeat_level: int = DEFAULT_ANTI_REPEAT_LEVEL,
    gemini_preflight_enabled: bool = DEFAULT_GEMINI_PREFLIGHT_ENABLED,
    gemini_preflight_timeout_seconds: int = DEFAULT_GEMINI_PREFLIGHT_TIMEOUT_SECONDS,
    gemini_rewrite_timeout_seconds: int = DEFAULT_GEMINI_REWRITE_TIMEOUT_SECONDS,
    fallback_timeout_seconds: int = DEFAULT_REWRITE_FALLBACK_TIMEOUT_SECONDS,
    section_index: int = 0,
    section_count: int = 0,
    introduced_concepts: List[str] = None,
    extra_instruction: Optional[str] = None,
) -> Tuple[str, List[Dict[str, str]], List[Dict[str, str]], bool, Optional[str], str]:
    """Rewrite a section into storyteller style.
    
    Returns: (story_text, terms, formula_explanations, success, error)
    """
    text = source_text.strip()
    if not text:
        return "", [], [], False, None, ""
    if len(text) < 80:
        return text, [], [], False, None, ""

    prompt = _build_story_prompt(
        section_title=section_title,
        source_text=text,
        style=style,
        response_format=rewrite_response_format,
        style_params=style_params,
        concise_level=concise_level,
        anti_repeat_level=anti_repeat_level,
        section_index=section_index,
        section_count=section_count,
        introduced_concepts=introduced_concepts,
        extra_instruction=extra_instruction,
    )
    formulas = _extract_latex_expressions(text)

    used_model = model
    try:
        if model.startswith("models/"):
            if gemini_preflight_enabled:
                preflight_ok, preflight_reason = _gemini_rewrite_preflight(
                    model=model,
                    timeout=int(gemini_preflight_timeout_seconds),
                )
                if not preflight_ok:
                    raise RuntimeError(f"Gemini preflight failed: {preflight_reason}")
            rewritten = _call_gemini_llm(
                prompt=prompt,
                model=model,
                timeout=int(gemini_rewrite_timeout_seconds),
            )
        else:
            rewritten = _call_local_llm(
                prompt=prompt,
                model=model,
                ollama_base_url=ollama_base_url,
                timeout=int(fallback_timeout_seconds),
            )
    except Exception as exc:
        primary_failure = f"{type(exc).__name__}: {exc}"
        _errors = [f"primary ({model}): {primary_failure}"]
        _succeeded = False
        for spec in (fallback_chain or []):
            fb_model = spec.get("model", "")
            fb_provider = (spec.get("provider") or "").strip().lower()
            fb_ollama_url = spec.get("ollama_base_url") or ollama_base_url
            fb_minimax_base = spec.get("minimax_base_url") or minimax_base_url
            fb_minimax_token = spec.get("minimax_oauth_token") or minimax_oauth_token
            try:
                if fb_provider in {"minimax.io", "minimax", "minimax-portal"}:
                    rewritten = _call_minimax_portal_llm(
                        prompt=prompt,
                        model=fb_model,
                        oauth_token=fb_minimax_token,
                        base_url=fb_minimax_base,
                        timeout=int(fallback_timeout_seconds),
                    )
                else:
                    rewritten = _call_local_llm(
                        prompt=prompt,
                        model=fb_model,
                        ollama_base_url=fb_ollama_url,
                        timeout=int(fallback_timeout_seconds),
                    )
                used_model = fb_model
                fallback_note = (
                    f"primary rewrite model failed ({primary_failure}); "
                    f"used fallback {fb_provider}:{fb_model}"
                )
                _succeeded = True
                break
            except Exception as fb_exc:
                _errors.append(f"{fb_provider or 'ollama'}:{fb_model}: {type(fb_exc).__name__}: {fb_exc}")
        if not _succeeded:
            return text, [], [], False, "; ".join(_errors), ""
    else:
        fallback_note = None

    story_text, terms, formula_explanations = _parse_rewrite_response(
        rewritten=rewritten,
        fallback_text=text,
    )

    story_text, removed_repeats = _deduplicate_story_text(
        story_text,
        anti_repeat_level=anti_repeat_level,
    )
    dedup_note = None
    if removed_repeats > 0:
        dedup_note = f"dedup removed {removed_repeats} repetitive blocks"

    # Check for missing formulas
    missing_formula_note = None
    if formulas:
        missing = [formula for formula in formulas if formula not in story_text]
        if missing:
            if append_missing_formulas:
                story_text = story_text.rstrip() + "\n\n公式保留：\n" + "\n".join(missing)
            else:
                missing_formula_note = (
                    f"detected {len(missing)} source formulas not echoed literally; "
                    "auto-append disabled"
                )

    note = _merge_notes(fallback_note, missing_formula_note)
    note = _merge_notes(note, dedup_note)
    return story_text, terms, formula_explanations, True, note, used_model


def _build_story_prompt(
    *,
    section_title: str,
    source_text: str,
    style: str,
    response_format: str,
    style_params: Dict[str, Any] = None,
    concise_level: int = DEFAULT_CONCISE_LEVEL,
    anti_repeat_level: int = DEFAULT_ANTI_REPEAT_LEVEL,
    section_index: int = 0,
    section_count: int = 0,
    introduced_concepts: List[str] = None,
    extra_instruction: Optional[str] = None,
) -> str:
    source_text = source_text.strip()
    style_key = _normalize_style(style)
    response_format = _normalize_rewrite_response_format(response_format)
    style_hint = _get_style_prompt(style_key, style_params)
    concise_value = max(0, min(10, int(concise_level)))
    anti_repeat_value = max(0, min(10, int(anti_repeat_level)))
    paragraph_sentence_cap = max(2, 7 - (concise_value // 2))
    max_short_sections = max(2, 6 - (concise_value // 3))

    # Build section positioning block (only show when context is available)
    section_context_block = ""
    if section_index > 0 and section_count > 0:
        section_context_block = f"\n章節定位：本節是全文第 {section_index}/{section_count} 節。"
    if introduced_concepts:
        concepts_str = "、".join(introduced_concepts[:40])  # cap to avoid token bloat
        section_context_block += (
            f"\n【強制禁止重複解釋】以下術語在前面章節已完整解釋過，本節絕對禁止再次定義或括號解釋，"
            f"直接使用術語本身即可（違反此規則會導致輸出被截斷）：\n{concepts_str}"
        )

    base_prompt = f"""你是頂尖的論文說書人，請把論文段落改寫成「易懂、可信、具教學感」的繁體中文說明。{section_context_block}

改寫風格：
{style_hint}

規則：
1. 保留原文技術重點，不要發明新實驗數據。
2. 保留所有數學式的 LaTeX 分隔符與內容，包含 $...$、$$...$$、\\(...\\)、\\[...\\]，不可改成 Unicode 偽公式。
3. 請優先說明「為什麼」：為什麼要這樣設計、為什麼這會有效、相比直覺做法差在哪裡。
4. 類比是橋樑：用類比把讀者引到技術概念入口後，要快速說清楚技術機制本身。同一個類比在整篇文章只展開一次。
5. 若內容含有任何公式，必須同時做到三件事：
   - 說明這個公式在做什麼數學操作，以及為什麼這個操作能達成所描述的目標（因果說明）。
   - 逐一解釋各變數的白話含義。
   - 提供至少一個具體數值代入例子，並說明結果的實務意義。
6. 可使用 Markdown 結構強化可讀性（例如 `**重點**`、清單、短小標），但不要過度冗長。
7. 如果原文太破碎，先做最小整理再說明，但不要脫離原意。
8. 可視需要加入簡短開場白、懸念或生活化一句話，以吸引讀者並提高閱讀興趣；避免空洞寒暄，亦避免制式套話（例如只說「好的」卻不進入正文）。
9. 精簡度：{concise_value}/10。優先保留資訊增量，刪除同義重述與口語贅詞；每段最多 {paragraph_sentence_cap} 句，整段小節最多 {max_short_sections} 個主要小段。
10. 重複抑制度：{anti_repeat_value}/10。若前文已解釋過同一概念，除非補充新資訊，否則不可再次定義；避免重複使用相同句型開頭；生活類比與故事主線亦應避免與前序章節過度近似（與風格提示中的自評呼應）。

章節標題：
{section_title}

原文段落：
{source_text}

"""
    extra = (extra_instruction or "").strip()
    if extra:
        base_prompt += f"\n\n【稽核／額外指示（必須遵守）】\n{extra}\n"

    if response_format == "json":
        return base_prompt + """

請用以下 JSON 格式輸出（務必嚴格遵守 JSON 語法）：
{{
    "story_text": "改寫後的說書內容（Markdown 格式）",
    "terms": [
        {{"term": "術語1", "explanation": "白話解釋"}}
    ],
    "formula_explanations": [
        {{
            "formula": "原始 LaTeX 公式",
            "explanation": "白話解釋（變數代表什麼）",
            "numerical_example": "數值範例演示（帶入數字、計算過程、意義解讀）"
        }}
    ]
}}

重要：terms 陣列中只列出本節「首次出現」且不在【強制禁止重複解釋】清單中的術語。已禁止清單的術語一律不得出現在 terms 中。

請直接輸出 JSON："""

    return base_prompt + """
請輸出乾淨的 Markdown（依「改寫風格」區塊：可能含改寫正文、對照表、自評文字與最末 EVAL 註解行；說書人風格務必遵守該區塊規定的輸出順序）。
不要輸出 JSON。
不要輸出 code fence。
不要輸出 ```markdown 或 ```json。
若有公式，請直接把公式自然保留在正文中，使用原本的 LaTeX 定界符。
若需要整理術語、變數意義、公式對照或數值示例，可直接在正文中使用 Markdown 表格呈現。
若使用 Markdown 表格，請確保欄位名稱清楚、單格內容不要過長，並避免輸出表格以外的結構化包裝。"""


def _post_rewrite_audit_openings_and_batches(
    rendered_sections: List[Dict[str, Any]],
) -> Tuple[str, List[List[Dict[str, Any]]]]:
    """Build per-section opening excerpts and char-capped batches for post-rewrite LLM audit."""
    openings_block = "\n".join(
        f"- 第{int(row.get('index', 0))}節《{str(row.get('title', ''))[:80]}》開頭摘錄："
        f"{repr((str(row.get('story_text', '') or '')[:120]).replace(chr(10), ' '))}"
        for row in rendered_sections
    )
    batches: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    cur_chars = 0
    for row in rendered_sections:
        chunk = len(str(row.get("source_text", ""))) + len(str(row.get("story_text", "")))
        if cur and (cur_chars + chunk > POST_REWRITE_AUDIT_BATCH_MAX_CHARS or len(cur) >= 2):
            batches.append(cur)
            cur = []
            cur_chars = 0
        cur.append(row)
        cur_chars += chunk
    if cur:
        batches.append(cur)
    return openings_block, batches


def _post_rewrite_storyteller_audit(
    rendered_sections: List[Dict[str, Any]],
    *,
    primary_model: str,
    fallback_chain: List[Dict[str, str]],
    ollama_base_url: str,
    minimax_base_url: str,
    minimax_oauth_token: str,
    rewrite_response_format: str,
    append_missing_formulas: bool,
    style_params: Dict[str, Any],
    concise_level: int,
    anti_repeat_level: int,
    gemini_preflight_enabled: bool,
    gemini_preflight_timeout_seconds: int,
    gemini_rewrite_timeout_seconds: int,
    fallback_timeout_seconds: int,
    llm_failures: List[str],
) -> None:
    """One LLM audit pass after all sections: fix or rewrite sections that fail audit (storyteller only)."""
    if not rendered_sections:
        return

    openings_block, batches = _post_rewrite_audit_openings_and_batches(rendered_sections)

    audit_timeout = max(int(gemini_rewrite_timeout_seconds), 120)

    for bi, batch in enumerate(batches, start=1):
        payload = [
            {
                "index": int(row.get("index", 0)),
                "title": str(row.get("title", "")),
                "source_text": str(row.get("source_text", "")),
                "story_text": str(row.get("story_text", "")),
            }
            for row in batch
        ]
        batch_json = json.dumps(payload, ensure_ascii=False)
        prompt = f"""你是論文「說書人」改稿的總編輯稽核員。以下 JSON 陣列是本批 {len(batch)} 節的原文 source_text 與改稿 story_text（Markdown）。

【全篇各節開頭摘錄（供比對套語／情節是否過度重複）】
{openings_block}

【稽核標準（逐節檢查）】
1. 忠於原文：無捏造數據／實驗／引用；無與原文明顯矛盾。
2. 原文中的 LaTeX 公式（$...$、$$...$$ 等）應出現在改稿適當位置（可出現在表格或正文），不可無故消失。
3. 說書人規格：改稿須含「本節專業術語白話對照」與「本節公式白話解釋」兩類 Markdown 表（標題用語可同義）；最末須有 <!-- EVAL: 類比[有/無] | 為什麼[有/無] | 虛構[無/有] | 前文情節或類比重複[無/有] | 術語表檢核[通過/未通過] | 公式白話表檢核[通過/未通過] --> 格式的自評行。
4. 跨節：開頭套語或懸念句型勿與其他節高度雷同；術語／公式對照表勿與前序節無意義重複堆砌。
5. 若某節已完全符合，passes 為 true，issues 空陣列，replacement_story_markdown 為 null。

【本批待審 JSON】
{batch_json}

請只輸出一個 JSON 物件（不要 markdown code fence），格式嚴格如下：
{{"results":[{{"index":<整數>,"passes":true,"issues":[],"replacement_story_markdown":null}},{{"index":<整數>,"passes":false,"issues":["..."],"replacement_story_markdown":"<若 passes 為 false，請給完整可取代該節的 Markdown 改稿全文；若 passes 為 true 則 null>"}}]}}

規則：passes=false 時，replacement_story_markdown 必須是非空字串，且須為該節完整改稿（含表與自評與 EVAL 行），可直接覆蓋原 story_text。"""

        try:
            raw, _used = _complete_prompt_with_model_fallback(
                prompt=prompt,
                model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=audit_timeout,
                fallback_timeout_seconds=fallback_timeout_seconds,
            )
        except Exception as exc:
            llm_failures.append(f"post_rewrite_audit batch {bi}: {type(exc).__name__}: {exc}")
            continue

        parsed = _try_parse_rewrite_payload(raw) or {}
        results = parsed.get("results")
        if not isinstance(results, list):
            llm_failures.append(f"post_rewrite_audit batch {bi}: invalid JSON shape")
            continue

        by_index = {int(row.get("index", 0)): row for row in rendered_sections}
        for item in results:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("index", 0))
            row = by_index.get(idx)
            if not row:
                continue
            passes = bool(item.get("passes", True))
            issues = item.get("issues") or []
            if not isinstance(issues, list):
                issues = []
            replacement = item.get("replacement_story_markdown")
            if passes:
                continue
            issues_txt = "; ".join(str(x) for x in issues if str(x).strip())
            if isinstance(replacement, str) and replacement.strip():
                row["story_text"] = replacement.strip()
                row["terms"] = []
                row["formula_explanations"] = []
                llm_failures.append(f"post_rewrite_audit: section {idx} revised in batch {bi} ({issues_txt or 'see replacement'})")
                continue

            title = str(row.get("title", ""))
            source = str(row.get("source_text", ""))
            extra = (
                "總編輯稽核未通過，請依下列問題重寫本節（須完整符合說書人風格與兩表＋自評＋EVAL）：\n"
                + (issues_txt or "未註明具體問題，請自行對照稽核標準全面檢查。")
            )
            fixed, terms, fexps, ok, err, _um = _rewrite_section(
                section_title=title,
                source_text=source,
                model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                style="storyteller",
                rewrite_response_format=rewrite_response_format,
                append_missing_formulas=append_missing_formulas,
                style_params=style_params,
                concise_level=concise_level,
                anti_repeat_level=anti_repeat_level,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=gemini_rewrite_timeout_seconds,
                fallback_timeout_seconds=fallback_timeout_seconds,
                section_index=idx,
                section_count=len(rendered_sections),
                introduced_concepts=None,
                extra_instruction=extra,
            )
            if ok and fixed.strip():
                row["story_text"] = fixed.strip()
                row["terms"] = terms or []
                row["formula_explanations"] = fexps or []
                llm_failures.append(
                    f"post_rewrite_audit: section {idx} re-rewritten after audit batch {bi} ({issues_txt or 'no replacement from audit'})"
                )
            elif err:
                llm_failures.append(f"post_rewrite_audit: section {idx} re-rewrite failed: {err}")


def _post_rewrite_blog_audit(
    rendered_sections: List[Dict[str, Any]],
    *,
    primary_model: str,
    fallback_chain: List[Dict[str, str]],
    ollama_base_url: str,
    minimax_base_url: str,
    minimax_oauth_token: str,
    rewrite_response_format: str,
    append_missing_formulas: bool,
    style_params: Dict[str, Any],
    concise_level: int,
    anti_repeat_level: int,
    gemini_preflight_enabled: bool,
    gemini_preflight_timeout_seconds: int,
    gemini_rewrite_timeout_seconds: int,
    fallback_timeout_seconds: int,
    llm_failures: List[str],
) -> None:
    """One LLM audit pass after all sections: fix or rewrite sections that fail audit (blog only)."""
    if not rendered_sections:
        return

    openings_block, batches = _post_rewrite_audit_openings_and_batches(rendered_sections)

    audit_timeout = max(int(gemini_rewrite_timeout_seconds), 120)

    for bi, batch in enumerate(batches, start=1):
        payload = [
            {
                "index": int(row.get("index", 0)),
                "title": str(row.get("title", "")),
                "source_text": str(row.get("source_text", "")),
                "story_text": str(row.get("story_text", "")),
            }
            for row in batch
        ]
        batch_json = json.dumps(payload, ensure_ascii=False)
        prompt = f"""你是論文「科普部落格」改稿的總編輯稽核員。以下 JSON 陣列是本批 {len(batch)} 節的原文 source_text 與改稿 story_text（Markdown）。

【全篇各節開頭摘錄（供比對引入句是否與他節過度重複）】
{openings_block}

【稽核標準（逐節檢查）】
1. 忠於原文：無捏造數據／實驗／引用；無與原文明顯矛盾。
2. 公式與範例完整性：原文中的所有 LaTeX 公式（$...$、$$...$$、\\(...\\)、\\[...\\] 等）以及具體實驗／數值／範例敘述，必須在改稿中完整保留並**無縫融入**正文，不可無故消失或僅口號式帶過；並以淺顯易懂的一句或多句點出式子與範例的意涵。
3. 結構與節奏：整節應具**起、承、轉、合**；並符合部落格約定：有吸引一般讀者的**標題取向**與**小標分段**；須有**前言層次**（約 2–4 句說明為何值得讀，可與開頭段合併但不可完全缺席）；內文各小段以**每段約 3–5 句**為主；**結語**須含重點整理並附**一個延伸思考問題**。
4. **本節專業術語白話解釋表（必核）**：在**該節** `story_text` 的**節末**（**本節**結語之後）、且於**該節** EVAL 行之前，必須有一則標題清楚之 Markdown 表格（建議欄位「術語｜白話說明」或同義欄名），涵蓋本節應交代之術語／縮寫；單格不宜過長；**禁止**整表留空或僅「無」敷衍。勿誤解為「整篇 HTML 全文最後一節才附表」——**每一節的改稿字串末端**皆須自帶此表（稽核時逐節檢查 `story_text`）。跨節勿無意義重複前序節已長篇定義之術語。
5. 風格：輕鬆、詼諧、自然、清楚、有吸引力，但**不浮誇、不聳動**。
6. 自評行：**該節** `story_text` 的**最末一行**須為 EVAL，且格式必須可機讀如下（括號內僅能有/無）：
<!-- EVAL: 讀者關聯[有/無] | 直覺解釋[有/無] | 輕鬆詼諧[有/無] -->
7. 跨節：開頭引入句型勿與其他節高度雷同；避免機械式複製前序節套話。
8. 若某節已完全符合，passes 為 true，issues 空陣列，replacement_story_markdown 為 null。

【本批待審 JSON】
{batch_json}

請只輸出一個 JSON 物件（不要 markdown code fence），格式嚴格如下：
{{"results":[{{"index":<整數>,"passes":true,"issues":[],"replacement_story_markdown":null}},{{"index":<整數>,"passes":false,"issues":["..."],"replacement_story_markdown":"<若 passes 為 false，請給完整可取代該節的 Markdown 改稿全文；若 passes 為 true 則 null>"}}]}}

規則：passes=false 時，replacement_story_markdown 必須是非空字串，且須為**該單節**完整改稿（含結語、**本節末尾術語白話表**、**該節**最末一行 EVAL），可直接覆蓋該節之 story_text。"""

        try:
            raw, _used = _complete_prompt_with_model_fallback(
                prompt=prompt,
                model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=audit_timeout,
                fallback_timeout_seconds=fallback_timeout_seconds,
            )
        except Exception as exc:
            llm_failures.append(
                f"post_rewrite_blog_audit batch {bi}: {type(exc).__name__}: {exc}"
            )
            continue

        parsed = _try_parse_rewrite_payload(raw) or {}
        results = parsed.get("results")
        if not isinstance(results, list):
            llm_failures.append(f"post_rewrite_blog_audit batch {bi}: invalid JSON shape")
            continue

        by_index = {int(row.get("index", 0)): row for row in rendered_sections}
        for item in results:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("index", 0))
            row = by_index.get(idx)
            if not row:
                continue
            passes = bool(item.get("passes", True))
            issues = item.get("issues") or []
            if not isinstance(issues, list):
                issues = []
            replacement = item.get("replacement_story_markdown")
            if passes:
                continue
            issues_txt = "; ".join(str(x) for x in issues if str(x).strip())
            if isinstance(replacement, str) and replacement.strip():
                row["story_text"] = replacement.strip()
                row["terms"] = []
                row["formula_explanations"] = []
                llm_failures.append(
                    f"post_rewrite_blog_audit: section {idx} revised in batch {bi} ({issues_txt or 'see replacement'})"
                )
                continue

            title = str(row.get("title", ""))
            source = str(row.get("source_text", ""))
            extra = (
                "總編輯稽核未通過，請依下列問題重寫本節（須完整符合科普部落格風格：起承轉合、公式與範例不漏、"
                "標題／小標／前言／結語與延伸問題、**本節末尾（結語後）術語白話表**、**本節最末一行** EVAL 三欄）：\n"
                + (issues_txt or "未註明具體問題，請自行對照稽核標準全面檢查。")
            )
            fixed, terms, fexps, ok, err, _um = _rewrite_section(
                section_title=title,
                source_text=source,
                model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                style="blog",
                rewrite_response_format=rewrite_response_format,
                append_missing_formulas=append_missing_formulas,
                style_params=style_params,
                concise_level=concise_level,
                anti_repeat_level=anti_repeat_level,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=gemini_rewrite_timeout_seconds,
                fallback_timeout_seconds=fallback_timeout_seconds,
                section_index=idx,
                section_count=len(rendered_sections),
                introduced_concepts=None,
                extra_instruction=extra,
            )
            if ok and fixed.strip():
                row["story_text"] = fixed.strip()
                row["terms"] = terms or []
                row["formula_explanations"] = fexps or []
                llm_failures.append(
                    f"post_rewrite_blog_audit: section {idx} re-rewritten after audit batch {bi} "
                    f"({issues_txt or 'no replacement from audit'})"
                )
            elif err:
                llm_failures.append(
                    f"post_rewrite_blog_audit: section {idx} re-rewrite failed: {err}"
                )


def _post_rewrite_professor_audit(
    rendered_sections: List[Dict[str, Any]],
    *,
    primary_model: str,
    fallback_chain: List[Dict[str, str]],
    ollama_base_url: str,
    minimax_base_url: str,
    minimax_oauth_token: str,
    rewrite_response_format: str,
    append_missing_formulas: bool,
    style_params: Dict[str, Any],
    concise_level: int,
    anti_repeat_level: int,
    gemini_preflight_enabled: bool,
    gemini_preflight_timeout_seconds: int,
    gemini_rewrite_timeout_seconds: int,
    fallback_timeout_seconds: int,
    llm_failures: List[str],
) -> None:
    """One LLM audit pass after all sections: fix or rewrite sections that fail audit (professor only)."""
    if not rendered_sections:
        return

    openings_block, batches = _post_rewrite_audit_openings_and_batches(rendered_sections)

    audit_timeout = max(int(gemini_rewrite_timeout_seconds), 120)

    for bi, batch in enumerate(batches, start=1):
        payload = [
            {
                "index": int(row.get("index", 0)),
                "title": str(row.get("title", "")),
                "source_text": str(row.get("source_text", "")),
                "story_text": str(row.get("story_text", "")),
            }
            for row in batch
        ]
        batch_json = json.dumps(payload, ensure_ascii=False)
        prompt = f"""你是論文「大教授／講義體」改稿的總編輯稽核員。以下 JSON 陣列是本批 {len(batch)} 節的原文 source_text 與改稿 story_text（Markdown）。

【全篇各節開頭摘錄（供比對套語是否與他節過度重複）】
{openings_block}

【稽核標準（逐節檢查）】
1. 忠於原文：無捏造數據／實驗／引用；無與原文明顯矛盾。
2. 公式完整性：原文中的 LaTeX 公式（$...$、$$...$$、\\(...\\)、\\[...\\] 等）須出現在改稿適當位置，不可無故消失。
3. **講義結構**：改稿須能對應教學講義脈絡，涵蓋或清楚呼應「主題、學習目標、背景意識、定義、原理、實例、比較限制、重點整理」等區塊（可用小標與分層條列）；**不可**退化成故事化口吻或部落格式聊天。
4. **學習強化元素（擇要檢核）**：視本節內容應適度出現對學習有幫助之**數值範例逐步演算**、**分點**、**比較**、**常見誤解**、**注意事項**、**重點整理**、**助憶金句**等；若原文有具體數值／實驗而改稿完全未以分步或表格輔助理解，可判未達標（勿要求無中生有）。
5. **本節專業術語白話解釋表（必核）**：在**該節** `story_text` 之**節末**、且於 EVAL 行之前，須有標題清楚之 Markdown 術語表（建議「術語｜白話說明」）；**禁止**整表留空或僅「無」敷衍。
6. **本節公式意義白話解釋表（必核）**：同上位置須有第二則 Markdown 表（建議「公式（LaTeX 原文）｜意義與變數白話說明」）；若本節無須獨立展開之新公式，表中須註明並簡述與前序式子之銜接，**不可**省略此表。
7. 自評行：**該節** `story_text` **最末一行**須為：
<!-- EVAL: 條理分層[有/無] | 預先定義[有/無] -->
8. 跨節：開頭套語勿與其他節高度雷同。
9. 若某節已完全符合，passes 為 true，issues 空陣列，replacement_story_markdown 為 null。

【本批待審 JSON】
{batch_json}

請只輸出一個 JSON 物件（不要 markdown code fence），格式嚴格如下：
{{"results":[{{"index":<整數>,"passes":true,"issues":[],"replacement_story_markdown":null}},{{"index":<整數>,"passes":false,"issues":["..."],"replacement_story_markdown":"<若 passes 為 false，請給完整可取代該節的 Markdown 改稿全文；若 passes 為 true 則 null>"}}]}}

規則：passes=false 時，replacement_story_markdown 必須是非空字串，且須為**該單節**完整改稿（含講義結構、**兩表**、**該節**最末一行 EVAL），可直接覆蓋該節之 story_text。"""

        try:
            raw, _used = _complete_prompt_with_model_fallback(
                prompt=prompt,
                model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=audit_timeout,
                fallback_timeout_seconds=fallback_timeout_seconds,
            )
        except Exception as exc:
            llm_failures.append(
                f"post_rewrite_professor_audit batch {bi}: {type(exc).__name__}: {exc}"
            )
            continue

        parsed = _try_parse_rewrite_payload(raw) or {}
        results = parsed.get("results")
        if not isinstance(results, list):
            llm_failures.append(f"post_rewrite_professor_audit batch {bi}: invalid JSON shape")
            continue

        by_index = {int(row.get("index", 0)): row for row in rendered_sections}
        for item in results:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("index", 0))
            row = by_index.get(idx)
            if not row:
                continue
            passes = bool(item.get("passes", True))
            issues = item.get("issues") or []
            if not isinstance(issues, list):
                issues = []
            replacement = item.get("replacement_story_markdown")
            if passes:
                continue
            issues_txt = "; ".join(str(x) for x in issues if str(x).strip())
            if isinstance(replacement, str) and replacement.strip():
                row["story_text"] = replacement.strip()
                row["terms"] = []
                row["formula_explanations"] = []
                llm_failures.append(
                    f"post_rewrite_professor_audit: section {idx} revised in batch {bi} ({issues_txt or 'see replacement'})"
                )
                continue

            title = str(row.get("title", ""))
            source = str(row.get("source_text", ""))
            extra = (
                "總編輯稽核未通過，請依下列問題重寫本節（須完整符合大教授／講義體：八段教學結構、學習強化元素擇要、"
                "**本節末尾兩表**（術語白話＋公式意義白話）、**本節最末一行** EVAL）：\n"
                + (issues_txt or "未註明具體問題，請自行對照稽核標準全面檢查。")
            )
            fixed, terms, fexps, ok, err, _um = _rewrite_section(
                section_title=title,
                source_text=source,
                model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                style="professor",
                rewrite_response_format=rewrite_response_format,
                append_missing_formulas=append_missing_formulas,
                style_params=style_params,
                concise_level=concise_level,
                anti_repeat_level=anti_repeat_level,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=gemini_rewrite_timeout_seconds,
                fallback_timeout_seconds=fallback_timeout_seconds,
                section_index=idx,
                section_count=len(rendered_sections),
                introduced_concepts=None,
                extra_instruction=extra,
            )
            if ok and fixed.strip():
                row["story_text"] = fixed.strip()
                row["terms"] = terms or []
                row["formula_explanations"] = fexps or []
                llm_failures.append(
                    f"post_rewrite_professor_audit: section {idx} re-rewritten after audit batch {bi} "
                    f"({issues_txt or 'no replacement from audit'})"
                )
            elif err:
                llm_failures.append(
                    f"post_rewrite_professor_audit: section {idx} re-rewrite failed: {err}"
                )


def _post_rewrite_fairy_audit(
    rendered_sections: List[Dict[str, Any]],
    *,
    primary_model: str,
    fallback_chain: List[Dict[str, str]],
    ollama_base_url: str,
    minimax_base_url: str,
    minimax_oauth_token: str,
    rewrite_response_format: str,
    append_missing_formulas: bool,
    style_params: Dict[str, Any],
    concise_level: int,
    anti_repeat_level: int,
    gemini_preflight_enabled: bool,
    gemini_preflight_timeout_seconds: int,
    gemini_rewrite_timeout_seconds: int,
    fallback_timeout_seconds: int,
    llm_failures: List[str],
) -> None:
    """One LLM audit pass after all sections: fix or rewrite sections that fail audit (fairy only)."""
    if not rendered_sections:
        return

    openings_block, batches = _post_rewrite_audit_openings_and_batches(rendered_sections)

    audit_timeout = max(int(gemini_rewrite_timeout_seconds), 120)

    for bi, batch in enumerate(batches, start=1):
        payload = [
            {
                "index": int(row.get("index", 0)),
                "title": str(row.get("title", "")),
                "source_text": str(row.get("source_text", "")),
                "story_text": str(row.get("story_text", "")),
            }
            for row in batch
        ]
        batch_json = json.dumps(payload, ensure_ascii=False)
        prompt = f"""你是論文「知識童話」改稿的總編輯稽核員。以下 JSON 陣列是本批 {len(batch)} 節的原文 source_text 與改稿 story_text（Markdown）。

【全篇各節開頭摘錄（供比對開場句是否與他節過度重複）】
{openings_block}

【稽核標準（逐節檢查）】
1. 忠於原文：無捏造數據／實驗／引用；無與原文明顯矛盾；原文未提及的資訊不得虛構。
2. 公式融入（必核）：原文中所有 LaTeX 公式（$...$、$$...$$、\\(...\\)、\\[...\\] 等）及具體數值範例，必須以「咒語」「石碑刻文」「魔法結界方程」等故事化包裝**無縫嵌入**情節，並在角色使用或解讀該式的過程中說清楚變數意義；**不可無故消失或僅口號式帶過**。
3. 故事結構：改稿須依序包含清楚小標題的五段：**故事標題**、**開場**、**情節發展**（含公式融入）、**問題如何解決**、**寓意與真實知識對應**（含映射摘要及公式真實身份）。
4. 核心知識保留：故事化改寫後，原文技術邏輯仍可從故事情節中清楚還原；角色與技術概念的映射須一致、不可前後矛盾。
5. **本節角色與概念對照表（必核）**：在**該節** `story_text` 的**節末**、且於 EVAL 行之前，須有標題清楚之 Markdown 表格（建議欄位「故事元素（角色／道具／地點）｜對應真實技術概念」）；須涵蓋本節新登場之故事元素；**禁止**整表留空或僅「無」敷衍；跨節勿無意義重複前序節已交代的映射。
6. **本節公式意義白話說明表（必核）**：緊接對照表之後（EVAL 行之前），須有第二則 Markdown 表格（建議欄位「公式（LaTeX 原文）｜故事中的身份與數學意義白話說明」）；若本節無新公式，表中須標明並簡述銜接關係，**不可**省略此表。
7. 開場多樣化：開場句型勿與其他節高度雷同（懸念、旁白、角色對白、場景描寫、反問、時間序等應輪替）。
8. 自評行：**該節** `story_text` **最末一行**須為（括號內僅能有/無/通過/未通過）：
<!-- EVAL: 角色映射[有/無] | 公式融入[有/無] | 核心知識保留[有/無] | 寓意說明[有/無] | 對照表檢核[通過/未通過] | 虛構[無/有] -->
9. 若某節已完全符合，passes 為 true，issues 空陣列，replacement_story_markdown 為 null。

【本批待審 JSON】
{batch_json}

請只輸出一個 JSON 物件（不要 markdown code fence），格式嚴格如下：
{{"results":[{{"index":<整數>,"passes":true,"issues":[],"replacement_story_markdown":null}},{{"index":<整數>,"passes":false,"issues":["..."],"replacement_story_markdown":"<若 passes 為 false，請給完整可取代該節的 Markdown 改稿全文；若 passes 為 true 則 null>"}}]}}

規則：passes=false 時，replacement_story_markdown 必須是非空字串，且須為**該單節**完整改稿（含五段故事結構、**兩表**、**該節**最末一行 EVAL），可直接覆蓋該節之 story_text。"""

        try:
            raw, _used = _complete_prompt_with_model_fallback(
                prompt=prompt,
                model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=audit_timeout,
                fallback_timeout_seconds=fallback_timeout_seconds,
            )
        except Exception as exc:
            llm_failures.append(
                f"post_rewrite_fairy_audit batch {bi}: {type(exc).__name__}: {exc}"
            )
            continue

        parsed = _try_parse_rewrite_payload(raw) or {}
        results = parsed.get("results")
        if not isinstance(results, list):
            llm_failures.append(f"post_rewrite_fairy_audit batch {bi}: invalid JSON shape")
            continue

        by_index = {int(row.get("index", 0)): row for row in rendered_sections}
        for item in results:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("index", 0))
            row = by_index.get(idx)
            if not row:
                continue
            passes = bool(item.get("passes", True))
            issues = item.get("issues") or []
            if not isinstance(issues, list):
                issues = []
            replacement = item.get("replacement_story_markdown")
            if passes:
                continue
            issues_txt = "; ".join(str(x) for x in issues if str(x).strip())
            if isinstance(replacement, str) and replacement.strip():
                row["story_text"] = replacement.strip()
                row["terms"] = []
                row["formula_explanations"] = []
                llm_failures.append(
                    f"post_rewrite_fairy_audit: section {idx} revised in batch {bi} ({issues_txt or 'see replacement'})"
                )
                continue

            title = str(row.get("title", ""))
            source = str(row.get("source_text", ""))
            extra = (
                "總編輯稽核未通過，請依下列問題重寫本節（須完整符合知識童話風格：五段故事結構含公式融入、"
                "**本節末尾兩表**（角色與概念對照＋公式意義白話說明）、**本節最末一行** EVAL 六欄）：\n"
                + (issues_txt or "未註明具體問題，請自行對照稽核標準全面檢查。")
            )
            fixed, terms, fexps, ok, err, _um = _rewrite_section(
                section_title=title,
                source_text=source,
                model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                style="fairy",
                rewrite_response_format=rewrite_response_format,
                append_missing_formulas=append_missing_formulas,
                style_params=style_params,
                concise_level=concise_level,
                anti_repeat_level=anti_repeat_level,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=gemini_rewrite_timeout_seconds,
                fallback_timeout_seconds=fallback_timeout_seconds,
                section_index=idx,
                section_count=len(rendered_sections),
                introduced_concepts=None,
                extra_instruction=extra,
            )
            if ok and fixed.strip():
                row["story_text"] = fixed.strip()
                row["terms"] = terms or []
                row["formula_explanations"] = fexps or []
                llm_failures.append(
                    f"post_rewrite_fairy_audit: section {idx} re-rewritten after audit batch {bi} "
                    f"({issues_txt or 'no replacement from audit'})"
                )
            elif err:
                llm_failures.append(
                    f"post_rewrite_fairy_audit: section {idx} re-rewrite failed: {err}"
                )


def _post_rewrite_lazy_audit(
    rendered_sections: List[Dict[str, Any]],
    *,
    primary_model: str,
    fallback_chain: List[Dict[str, str]],
    ollama_base_url: str,
    minimax_base_url: str,
    minimax_oauth_token: str,
    rewrite_response_format: str,
    append_missing_formulas: bool,
    style_params: Dict[str, Any],
    concise_level: int,
    anti_repeat_level: int,
    gemini_preflight_enabled: bool,
    gemini_preflight_timeout_seconds: int,
    gemini_rewrite_timeout_seconds: int,
    fallback_timeout_seconds: int,
    llm_failures: List[str],
) -> None:
    """One LLM audit pass after all sections: fix or rewrite sections that fail audit (lazy only)."""
    if not rendered_sections:
        return

    openings_block, batches = _post_rewrite_audit_openings_and_batches(rendered_sections)

    audit_timeout = max(int(gemini_rewrite_timeout_seconds), 120)

    for bi, batch in enumerate(batches, start=1):
        payload = [
            {
                "index": int(row.get("index", 0)),
                "title": str(row.get("title", "")),
                "source_text": str(row.get("source_text", "")),
                "story_text": str(row.get("story_text", "")),
            }
            for row in batch
        ]
        batch_json = json.dumps(payload, ensure_ascii=False)
        prompt = f"""你是論文「懶人包」改稿的總編輯稽核員。以下 JSON 陣列是本批 {len(batch)} 節的原文 source_text 與改稿 story_text（Markdown）。

【全篇各節開頭摘錄（供比對結論句是否與他節過度重複）】
{openings_block}

【稽核標準（逐節檢查）】
1. 忠於原文：無捏造數據／實驗／引用；無與原文明顯矛盾。
2. 公式保留（必核）：原文中所有 LaTeX 公式（$...$、$$...$$、\\(...\\)、\\[...\\] 等）及具體數值範例，必須出現在改稿中，以 `> 公式：$...$` 區塊或嵌入條列點的方式呈現，並附簡短意涵說明；**不可無故消失**。
3. 懶人包結構：改稿須依序包含：**一句話結論** → **背景／問題** → **重點條列**（每點 1-2 句）→（選用）**公式速查** → **限制或注意事項** → **一句帶走重點**；不得退化成長篇散文或故事化口吻。
4. 精簡不失真：重要限制、前提、結果不可為了精簡而刪除；術語首次出現須有一句白話解釋。
5. **本節術語速查表（必核）**：在**該節** `story_text` 的**節末**（EVAL 行之前），須有標題清楚之 Markdown 表格（欄位「術語｜一句白話」）；涵蓋本節應獨立對照之術語；**禁止**整表留空或僅「無」敷衍；跨節已詳解者可極短「見前節」。
6. **本節公式速查對照表（必核）**：緊接術語表之後（EVAL 行之前），須有第二則 Markdown 表格（欄位「公式（LaTeX 原文）｜意涵一句話」）；若本節無新公式，表中須標明並簡述銜接關係，**不可**省略此表。
7. 跨節：一句話結論句型勿與其他節高度雷同；術語不得無意義重複定義。
8. 自評行：**該節** `story_text` **最末一行**須為（括號內僅能有/無/通過/未通過）：
<!-- EVAL: 結論先行[有/無] | 條列摘要[有/無] | 公式保留[有/無] | 速查表檢核[通過/未通過] | 虛構[無/有] -->
9. 若某節已完全符合，passes 為 true，issues 空陣列，replacement_story_markdown 為 null。

【本批待審 JSON】
{batch_json}

請只輸出一個 JSON 物件（不要 markdown code fence），格式嚴格如下：
{{"results":[{{"index":<整數>,"passes":true,"issues":[],"replacement_story_markdown":null}},{{"index":<整數>,"passes":false,"issues":["..."],"replacement_story_markdown":"<若 passes 為 false，請給完整可取代該節的 Markdown 改稿全文；若 passes 為 true 則 null>"}}]}}

規則：passes=false 時，replacement_story_markdown 必須是非空字串，且須為**該單節**完整改稿（含六段結構、**兩表**、**該節**最末一行 EVAL），可直接覆蓋該節之 story_text。"""

        try:
            raw, _used = _complete_prompt_with_model_fallback(
                prompt=prompt,
                model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=audit_timeout,
                fallback_timeout_seconds=fallback_timeout_seconds,
            )
        except Exception as exc:
            llm_failures.append(
                f"post_rewrite_lazy_audit batch {bi}: {type(exc).__name__}: {exc}"
            )
            continue

        parsed = _try_parse_rewrite_payload(raw) or {}
        results = parsed.get("results")
        if not isinstance(results, list):
            llm_failures.append(f"post_rewrite_lazy_audit batch {bi}: invalid JSON shape")
            continue

        by_index = {int(row.get("index", 0)): row for row in rendered_sections}
        for item in results:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("index", 0))
            row = by_index.get(idx)
            if not row:
                continue
            passes = bool(item.get("passes", True))
            issues = item.get("issues") or []
            if not isinstance(issues, list):
                issues = []
            replacement = item.get("replacement_story_markdown")
            if passes:
                continue
            issues_txt = "; ".join(str(x) for x in issues if str(x).strip())
            if isinstance(replacement, str) and replacement.strip():
                row["story_text"] = replacement.strip()
                row["terms"] = []
                row["formula_explanations"] = []
                llm_failures.append(
                    f"post_rewrite_lazy_audit: section {idx} revised in batch {bi} ({issues_txt or 'see replacement'})"
                )
                continue

            title = str(row.get("title", ""))
            source = str(row.get("source_text", ""))
            extra = (
                "總編輯稽核未通過，請依下列問題重寫本節（須完整符合懶人包格式：六段結構含公式速查、"
                "**本節末尾兩表**（術語速查＋公式速查）、**本節最末一行** EVAL 五欄）：\n"
                + (issues_txt or "未註明具體問題，請自行對照稽核標準全面檢查。")
            )
            fixed, terms, fexps, ok, err, _um = _rewrite_section(
                section_title=title,
                source_text=source,
                model=primary_model,
                fallback_chain=fallback_chain,
                ollama_base_url=ollama_base_url,
                minimax_base_url=minimax_base_url,
                minimax_oauth_token=minimax_oauth_token,
                style="lazy",
                rewrite_response_format=rewrite_response_format,
                append_missing_formulas=append_missing_formulas,
                style_params=style_params,
                concise_level=concise_level,
                anti_repeat_level=anti_repeat_level,
                gemini_preflight_enabled=gemini_preflight_enabled,
                gemini_preflight_timeout_seconds=gemini_preflight_timeout_seconds,
                gemini_rewrite_timeout_seconds=gemini_rewrite_timeout_seconds,
                fallback_timeout_seconds=fallback_timeout_seconds,
                section_index=idx,
                section_count=len(rendered_sections),
                introduced_concepts=None,
                extra_instruction=extra,
            )
            if ok and fixed.strip():
                row["story_text"] = fixed.strip()
                row["terms"] = terms or []
                row["formula_explanations"] = fexps or []
                llm_failures.append(
                    f"post_rewrite_lazy_audit: section {idx} re-rewritten after audit batch {bi} "
                    f"({issues_txt or 'no replacement from audit'})"
                )
            elif err:
                llm_failures.append(
                    f"post_rewrite_lazy_audit: section {idx} re-rewrite failed: {err}"
                )


def _normalize_style(style: Any) -> str:
    normalized = str(style or "").strip().lower()
    if normalized == "podcast":
        return "professor"
    if normalized in STYLE_PROMPTS:
        return normalized
    return DEFAULT_STYLE


def _complete_prompt_with_model_fallback(
    *,
    prompt: str,
    model: str,
    fallback_chain: List[Dict[str, str]],
    ollama_base_url: str,
    minimax_base_url: str,
    minimax_oauth_token: str,
    gemini_preflight_enabled: bool,
    gemini_preflight_timeout_seconds: int,
    gemini_rewrite_timeout_seconds: int,
    fallback_timeout_seconds: int,
) -> Tuple[str, str]:
    """Run a one-off text prompt on primary model with the same fallback chain as section rewrite."""
    used_model = model
    try:
        if model.startswith("models/"):
            if gemini_preflight_enabled:
                preflight_ok, preflight_reason = _gemini_rewrite_preflight(
                    model=model,
                    timeout=int(gemini_preflight_timeout_seconds),
                )
                if not preflight_ok:
                    raise RuntimeError(f"Gemini preflight failed: {preflight_reason}")
            rewritten = _call_gemini_llm(
                prompt=prompt,
                model=model,
                timeout=int(gemini_rewrite_timeout_seconds),
            )
        else:
            rewritten = _call_local_llm(
                prompt=prompt,
                model=model,
                ollama_base_url=ollama_base_url,
                timeout=int(fallback_timeout_seconds),
            )
        return rewritten, used_model
    except Exception as exc:
        primary_failure = f"{type(exc).__name__}: {exc}"
        for spec in (fallback_chain or []):
            fb_model = spec.get("model", "")
            fb_provider = (spec.get("provider") or "").strip().lower()
            fb_ollama_url = spec.get("ollama_base_url") or ollama_base_url
            fb_minimax_base = spec.get("minimax_base_url") or minimax_base_url
            fb_minimax_token = spec.get("minimax_oauth_token") or minimax_oauth_token
            try:
                if fb_provider in {"minimax.io", "minimax", "minimax-portal"}:
                    rewritten = _call_minimax_portal_llm(
                        prompt=prompt,
                        model=fb_model,
                        oauth_token=fb_minimax_token,
                        base_url=fb_minimax_base,
                        timeout=int(fallback_timeout_seconds),
                    )
                else:
                    rewritten = _call_local_llm(
                        prompt=prompt,
                        model=fb_model,
                        ollama_base_url=fb_ollama_url,
                        timeout=int(fallback_timeout_seconds),
                    )
                return rewritten, fb_model
            except Exception:
                continue
        raise RuntimeError(f"post-rewrite audit LLM failed ({primary_failure})") from exc


def _call_local_llm(*, prompt: str, model: str, ollama_base_url: str, timeout: int = 240) -> str:
    req = urllib.request.Request(
        f"{ollama_base_url}/api/generate",
        data=json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.2},
            }
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read())
    return str(payload.get("response", "")).strip()


def _call_gemini_llm(*, prompt: str, model: str, timeout: int = 240) -> str:
    from gemini_client import generate_content_text

    gemini_api_key = _configure_gemini()
    if not gemini_api_key:
        raise RuntimeError("missing GOOGLE_API_KEY/GEMINI_API_KEY")

    return generate_content_text(
        api_key=gemini_api_key,
        model=model,
        contents=prompt,
        timeout=int(timeout),
    )


def _gemini_rewrite_preflight(*, model: str, timeout: int) -> Tuple[bool, str]:
    now = time.time()
    cached = _GEMINI_PREFLIGHT_CACHE.get(model)
    if cached and now - cached[0] < GEMINI_PREFLIGHT_CACHE_TTL_SECONDS:
        return cached[1], cached[2]

    gemini_api_key = _configure_gemini()
    if not gemini_api_key:
        result = (False, "missing GOOGLE_API_KEY/GEMINI_API_KEY")
        _GEMINI_PREFLIGHT_CACHE[model] = (now, result[0], result[1])
        return result

    try:
        from gemini_client import generate_content_text

        generate_content_text(
            api_key=gemini_api_key,
            model=model,
            contents="回覆 OK",
            timeout=int(timeout),
        )
        result = (True, "ok")
    except Exception as exc:
        result = (False, f"{type(exc).__name__}: {exc}")

    _GEMINI_PREFLIGHT_CACHE[model] = (now, result[0], result[1])
    return result


def _call_minimax_portal_llm(*, prompt: str, model: str, oauth_token: str, base_url: str, timeout: int = 240) -> str:
    token = str(oauth_token or "").strip()
    if not token:
        raise RuntimeError("missing MINIMAX_PORTAL_OAUTH_TOKEN")

    normalized_base = str(base_url or DEFAULT_MINIMAX_PORTAL_BASE_URL).rstrip("/")
    endpoint_candidates: List[str] = []
    if normalized_base.endswith("/v1"):
        endpoint_candidates.append(f"{normalized_base}/text/chatcompletion_v2")
        endpoint_candidates.append(f"{normalized_base}/text/chatcompletion_pro")
    else:
        endpoint_candidates.append(f"{normalized_base}/v1/text/chatcompletion_v2")
        endpoint_candidates.append(f"{normalized_base}/v1/text/chatcompletion_pro")

    # chatcompletion_v2 uses standard OpenAI-compatible messages format.
    # chatcompletion_pro requires an additional bot_setting field.
    base_payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    pro_payload = {
        **base_payload,
        "bot_setting": [{"bot_name": "AI", "content": "You are a helpful assistant."}],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    all_errors: List[str] = []
    for endpoint in endpoint_candidates:
        payload_to_send = pro_payload if "chatcompletion_pro" in endpoint else base_payload
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload_to_send).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                payload = json.loads(response.read())
        except Exception as exc:
            all_errors.append(f"{endpoint}: {type(exc).__name__}: {exc}")
            continue

        provider_error = _extract_provider_error(payload)
        if provider_error:
            all_errors.append(f"{endpoint}: {provider_error}")
            continue

        text = _extract_text_from_chat_payload(payload)
        if text:
            return text
        all_errors.append(f"{endpoint}: empty response content")

    raise RuntimeError(" | ".join(all_errors) if all_errors else "minimax-portal call failed")


def _parse_rewrite_response(
    *,
    rewritten: str,
    fallback_text: str,
) -> Tuple[str, List[Dict[str, str]], List[Dict[str, str]]]:
    cleaned = _strip_thinking_block(rewritten).strip()
    if not cleaned:
        return fallback_text, [], []

    parsed = _try_parse_rewrite_payload(cleaned)
    if parsed is not None:
        story_text = str(parsed.get("story_text") or "").strip() or fallback_text
        terms = _normalize_dict_list(parsed.get("terms"))
        formula_explanations = _normalize_dict_list(parsed.get("formula_explanations"))
        return story_text, terms, formula_explanations

    extracted_story = _extract_json_string_field(cleaned, "story_text")
    if extracted_story:
        return extracted_story, [], []

    return cleaned or fallback_text, [], []


def _try_parse_rewrite_payload(text: str) -> Optional[Dict[str, Any]]:
    candidates = _rewrite_json_candidates(text)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _rewrite_json_candidates(text: str) -> List[str]:
    stripped = text.strip()
    candidates: List[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        candidate = value.strip()
        if not candidate or candidate in seen:
            return
        seen.add(candidate)
        candidates.append(candidate)

    _add(stripped)

    fenced_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", stripped, flags=re.IGNORECASE)
    for block in fenced_blocks:
        _add(block)

    decoder = json.JSONDecoder()
    for start_index, char in enumerate(stripped):
        if char != "{":
            continue
        try:
            parsed, end_index = decoder.raw_decode(stripped[start_index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            _add(stripped[start_index : start_index + end_index])

    return candidates


def _extract_json_string_field(text: str, field_name: str) -> str:
    pattern = rf'"{re.escape(field_name)}"\s*:\s*"((?:\\.|[^"\\])*)"'
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        return ""

    raw_value = match.group(1)
    try:
        decoded = json.loads(f'"{raw_value}"')
    except json.JSONDecodeError:
        decoded = raw_value.encode("utf-8", errors="ignore").decode("unicode_escape", errors="ignore")
    return str(decoded).strip()


def _normalize_dict_list(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []

    normalized: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized.append({str(key): str(val) for key, val in item.items()})
    return normalized


def _extract_text_from_chat_payload(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
                if isinstance(content, list):
                    parts = [
                        str(item.get("text", "")).strip()
                        for item in content
                        if isinstance(item, dict) and str(item.get("text", "")).strip()
                    ]
                    merged = "\n".join(parts).strip()
                    if merged:
                        return merged
            text = first.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()

    for key in ("reply", "output_text", "text", "content"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def _extract_provider_error(payload: Dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""

    base_resp = payload.get("base_resp")
    if isinstance(base_resp, dict):
        status_code = base_resp.get("status_code")
        status_msg = str(base_resp.get("status_msg") or "").strip()
        if status_code not in (None, 0, "0"):
            code_text = str(status_code)
            if status_msg:
                return f"provider error code={code_text}: {status_msg}"
            return f"provider error code={code_text}"

    error = payload.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "").strip()
        code = str(error.get("code") or "").strip()
        if code and message:
            return f"provider error {code}: {message}"
        if message:
            return f"provider error: {message}"

    return ""


def _strip_thinking_block(text: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return _strip_conversational_chatter(cleaned)


def _strip_conversational_chatter(text: str) -> str:
    lines = text.splitlines()
    chatter_patterns = (
        r"^(?:好的|好|沒問題|當然可以|當然|可以|以下(?:是|為)|以下提供|我將|我會|讓我們|先來|改寫如下|重寫如下|答案如下|說明如下|內容如下)\b",
        r"^(?:sure|certainly|absolutely|of course|here(?:'s| is)|below(?: is| are)|let'?s)\b",
    )
    leading_prefix = re.compile("|".join(f"(?:{pattern})" for pattern in chatter_patterns), flags=re.IGNORECASE)

    idx = 0
    while idx < len(lines):
        candidate = lines[idx].strip(" \t`#>*-")
        if not candidate:
            idx += 1
            continue
        if leading_prefix.match(candidate):
            idx += 1
            continue
        break

    cleaned = "\n".join(lines[idx:]).strip()
    cleaned = re.sub(
        r"^\s*(?:好的|好|沒問題|當然可以|當然|可以|以下(?:是|為)|以下提供|改寫如下|重寫如下|答案如下|說明如下|內容如下|sure|certainly|absolutely|of course|here(?:'s| is)|below(?: is| are))\s*[：:，,。\-\s]*",
        "",
        cleaned,
        count=1,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def _extract_latex_expressions(text: str) -> List[str]:
    patterns = [
        r"\\\[(.*?)\\\]",
        r"\$\$(.*?)\$\$",
        r"\\\((.*?)\\\)",
        r"(?<!\$)\$([^\$\n]{1,300}?)\$(?!\$)",
    ]
    hits: List[Tuple[int, str]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.DOTALL):
            expr = match.group(0).strip()
            if expr:
                hits.append((match.start(), expr))

    hits.sort(key=lambda item: item[0])
    unique: List[str] = []
    seen: set[str] = set()
    for _, expr in hits:
        if expr in seen:
            continue
        seen.add(expr)
        unique.append(expr)
    return unique


def _deduplicate_story_text(text: str, *, anti_repeat_level: int) -> Tuple[str, int]:
    level = max(0, min(10, int(anti_repeat_level)))
    if level < 4:
        return text, 0

    blocks = [b.strip() for b in re.split(r"\n\s*\n+", str(text or "").strip()) if b.strip()]
    if len(blocks) <= 1:
        return text, 0

    threshold = 0.90 - (level * 0.018)
    threshold = max(0.70, min(0.92, threshold))

    kept_blocks: List[str] = []
    removed = 0
    for block in blocks:
        normalized = _normalize_dedup_text(block)
        if not normalized:
            kept_blocks.append(block)
            continue
        if _is_structured_markdown_block(block):
            kept_blocks.append(block)
            continue
        is_repeat = False
        for existing in kept_blocks:
            if _is_structured_markdown_block(existing):
                continue
            existing_norm = _normalize_dedup_text(existing)
            if not existing_norm:
                continue
            ratio = _quick_similarity_ratio(normalized, existing_norm)
            if ratio >= threshold:
                is_repeat = True
                break
        if is_repeat:
            removed += 1
            continue
        kept_blocks.append(_dedupe_sentences_in_block(block, level))

    if not kept_blocks:
        return text, 0
    return "\n\n".join(kept_blocks).strip(), removed


def _normalize_dedup_text(text: str) -> str:
    normalized = str(text or "").lower()
    normalized = re.sub(r"`[^`]+`", "", normalized)
    normalized = re.sub(r"\$[^$]+\$", "", normalized)
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized)
    return normalized.strip()


def _quick_similarity_ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    if short and short in long:
        return len(short) / max(len(long), 1)
    overlap = len(set(_char_ngrams(left, 3)) & set(_char_ngrams(right, 3)))
    total = max(len(set(_char_ngrams(left, 3)) | set(_char_ngrams(right, 3))), 1)
    return overlap / total


def _char_ngrams(text: str, size: int) -> List[str]:
    if len(text) <= size:
        return [text]
    return [text[i : i + size] for i in range(0, len(text) - size + 1)]


def _is_structured_markdown_block(block: str) -> bool:
    line = str(block or "").lstrip()
    if not line:
        return False
    return bool(re.match(r"^(#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|\|.*\||```|>\s+)", line))


def _dedupe_sentences_in_block(block: str, level: int) -> str:
    if level < 6 or _is_structured_markdown_block(block):
        return block
    parts = re.split(r"(?<=[。！？.!?])\s+", block)
    kept: List[str] = []
    signatures: List[str] = []
    sentence_threshold = 0.86 - (level * 0.015)
    sentence_threshold = max(0.72, min(0.90, sentence_threshold))
    for sentence in parts:
        s = sentence.strip()
        if not s:
            continue
        sig = _normalize_dedup_text(s)
        if not sig:
            kept.append(s)
            continue
        repeated = any(_quick_similarity_ratio(sig, prev) >= sentence_threshold for prev in signatures if prev)
        if repeated:
            continue
        signatures.append(sig)
        kept.append(s)
    return " ".join(kept).strip() or block


def _resolve_title(payload: Dict[str, Any], pdf_path: Path, sections: List[Dict[str, Any]]) -> str:
    candidate = payload.get("title") or payload.get("paper_title")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()

    if sections:
        first_title = str(sections[0].get("title", "")).strip()
        if first_title and first_title.lower() not in {"overview", "content"}:
            return first_title
    return pdf_path.stem


def _safe_html_filename_segment(value: str, max_len: int = 120) -> str:
    """允許中英與常見字元；移除檔名不允許的字元。"""
    s = (value or "").strip()
    if not s:
        return ""
    forbidden = '\\/:*?"<>|\n\r\t'
    out: List[str] = []
    for ch in s:
        out.append("_" if ch in forbidden else ch)
    s = "".join(out)
    s = re.sub(r"[\s_]+", "_", s).strip("._-")
    if not s:
        return ""
    if len(s) > max_len:
        s = s[:max_len].rstrip("._-")
    return s or ""


def _truncate_utf8_bytes(s: str, max_bytes: int) -> str:
    if not s or max_bytes <= 0:
        return ""
    data = s.encode("utf-8")
    if len(data) <= max_bytes:
        return s
    n = max_bytes
    while n > 0:
        try:
            return data[:n].decode("utf-8").rstrip("._-")
        except UnicodeDecodeError:
            n -= 1
    return ""


def _build_output_path(
    pdf_path: Path,
    payload: Dict[str, Any],
    *,
    title: str,
    job: Dict[str, Any],
) -> Path:
    """預設檔名：論文標題_任務識別號.html（可經 payload 覆寫）。"""
    custom_name = payload.get("output_filename") or payload.get("output_name")
    if isinstance(custom_name, str) and custom_name.strip():
        filename = custom_name.strip()
        if not filename.lower().endswith(".html"):
            filename = f"{filename}.html"
        return STORYTELLERS_DIR / filename

    jid = str(job.get("job_id") or "").strip()
    jid_part = _safe_html_filename_segment(jid, max_len=80) or "unknown"
    title_part = _safe_html_filename_segment(title, max_len=120)
    if not title_part:
        title_part = _slugify(pdf_path.stem) or "paper"

    stem = f"{title_part}_{jid_part}"
    suffix = ".html"
    max_total_bytes = 240
    full = f"{stem}{suffix}"
    if len(full.encode("utf-8")) > max_total_bytes:
        overhead = len(f"_{jid_part}{suffix}".encode("utf-8"))
        budget = max_total_bytes - overhead
        title_part = _truncate_utf8_bytes(title_part, max(24, budget))
        if not title_part:
            title_part = "paper"
        stem = f"{title_part}_{jid_part}"
        full = f"{stem}{suffix}"
    return STORYTELLERS_DIR / full


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return slug or "story"


STYLE_DISPLAY_NAMES: Dict[str, str] = {
    "storyteller": "說書人版本",
    "blog":        "科普部落格版本",
    "professor":   "大教授版本（課堂講義）",
    "fairy":       "童話故事版本",
    "lazy":        "懶人包版本",
    "question":    "問題驅動版本",
    "log":         "實驗日誌版本",
}


def _build_story_html_document(
    *,
    title: str,
    pdf_path: Path,
    rendered_sections: List[Dict[str, Any]],
    model: str,
    style: str = DEFAULT_STYLE,
) -> str:
    toc_items: List[str] = []
    section_items: List[str] = []
    for section in rendered_sections:
        idx = section["index"]
        section_id = f"section-{idx}"
        safe_title = html.escape(str(section["title"]))
        toc_items.append(f'<li><a href="#{section_id}">Section {idx} - {safe_title}</a></li>')

        story_html = _text_to_html_blocks(str(section.get("story_text", "")))

        # Build terms table
        terms = section.get("terms", [])
        terms_html = ""
        if terms:
            term_rows = []
            for t in terms:
                term = html.escape(t.get("term", ""))
                explanation = html.escape(t.get("explanation", ""))
                term_rows.append(f"<tr><td><strong>{term}</strong></td><td>{explanation}</td></tr>")
            terms_html = f"""
        <div class="terms-box">
            <h3>📚 技術術語表</h3>
            <table class="term-table">
                <tr><th>術語</th><th>白話解釋</th></tr>
                {''.join(term_rows)}
            </table>
        </div>"""

        # Build formula explanations
        formula_expls = section.get("formula_explanations", [])
        formula_html = ""
        if formula_expls:
            formula_blocks = []
            for f in formula_expls:
                formula = html.escape(f.get("formula", ""))
                explanation = html.escape(f.get("explanation", ""))
                example = html.escape(f.get("numerical_example", ""))
                formula_blocks.append(f"""
            <div class="formula-box">
                <div class="formula-content">$${formula}$$</div>
                <div class="formula-explanation">
                    <strong>白話解釋：</strong>{explanation}
                </div>
                <div class="example-box">
                    <strong>📊 數值範例演示：</strong><br>
                    {example}
                </div>
            </div>""")
            formula_html = ''.join(formula_blocks)

        # storyteller style: dual-column layout (left: original, right: rewritten)
        if style == "storyteller":
            orig_html = _text_to_html_blocks(str(section.get("source_text", "")))
            section_items.append(
                f"""
    <section id="{section_id}">
        <h2>Section {idx} - {safe_title}</h2>
        <div class="dual-col">
            <div class="col-orig">
                <div class="col-label">📄 原文</div>
{orig_html}
            </div>
            <div class="col-plain">
                <div class="col-label">💬 說書人白話</div>
{story_html}
            </div>
        </div>
        {formula_html}
        {terms_html}
    </section>"""
            )
        else:
            section_items.append(
                f"""
    <section id="{section_id}">
        <h2>Section {idx} - {safe_title}</h2>
        <div class="story-block">
            <h3>📖 故事化改寫</h3>
{story_html}
        </div>
        {formula_html}
        {terms_html}
    </section>"""
            )

    generated_at = datetime.now(timezone.utc).isoformat()
    safe_title = html.escape(title)
    safe_pdf = html.escape(str(pdf_path))
    safe_model = html.escape(model)
    style_label = html.escape(STYLE_DISPLAY_NAMES.get(style, style))
    toc_html = "\n".join(toc_items)
    sections_html = "\n".join(section_items)

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{safe_title} - 說書人版</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
    <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
    <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
    <script>
    document.addEventListener("DOMContentLoaded", function() {{
      renderMathInElement(document.body, {{
        delimiters: [
          {{left: "$$", right: "$$", display: true}},
          {{left: "\\\\[", right: "\\\\]", display: true}},
          {{left: "$", right: "$", display: false}},
          {{left: "\\\\(", right: "\\\\)", display: false}}
        ],
        throwOnError: false
      }});
    }});
    </script>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: "Noto Sans TC", "PingFang TC", "Segoe UI", sans-serif;
            line-height: 1.8;
            max-width: 940px;
            margin: 0 auto;
            padding: 24px;
            background: #fafafa;
            color: #1e293b;
        }}
        h1 {{
            color: #1a1a2e;
            border-bottom: 4px solid #3b82f6;
            padding-bottom: 12px;
            margin-bottom: 8px;
            font-size: 28px;
        }}
        .meta {{
            color: #64748b;
            font-size: 13px;
            margin-bottom: 24px;
        }}
        .toc {{
            background: #f1f5f9;
            padding: 16px 20px;
            border-radius: 10px;
            margin-bottom: 28px;
        }}
        .toc h3 {{
            margin: 0 0 10px;
            color: #1d4ed8;
        }}
        .toc ul {{
            margin: 0;
            padding-left: 20px;
        }}
        .toc li {{
            margin: 6px 0;
        }}
        .toc a {{
            color: #1d4ed8;
            text-decoration: none;
        }}
        h2 {{
            color: #1d4ed8;
            margin-top: 40px;
            font-size: 22px;
            border-left: 5px solid #3b82f6;
            padding-left: 12px;
        }}
        h3 {{
            color: #0369a1;
            margin-top: 24px;
            margin-bottom: 12px;
        }}
        .story-block {{
            background: #ffffff;
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08);
            margin: 16px 0;
        }}
        /* 雙欄並列（說書人風格）*/
        .dual-col {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin: 16px 0;
            align-items: start;
        }}
        @media (max-width: 768px) {{
            .dual-col {{ grid-template-columns: 1fr; }}
        }}
        .col-orig, .col-plain {{
            border-radius: 12px;
            padding: 18px 20px;
        }}
        .col-orig {{
            background: #f1f5f9;
            border-left: 4px solid #94a3b8;
            color: #475569;
            font-size: 0.93em;
        }}
        .col-plain {{
            background: #ffffff;
            border-left: 4px solid #3b82f6;
            box-shadow: 0 2px 8px rgba(15, 23, 42, 0.08);
        }}
        .col-label {{
            font-size: 0.78em;
            font-weight: 700;
            letter-spacing: 0.05em;
            text-transform: uppercase;
            color: #64748b;
            margin-bottom: 10px;
        }}
        p {{
            margin: 14px 0;
            text-align: justify;
        }}
        .formula-box {{
            background: #f8fafc;
            border-left: 4px solid #3b82f6;
            border-radius: 8px;
            padding: 16px;
            margin: 16px 0;
        }}
        .formula-content {{
            background: #ffffff;
            padding: 12px;
            border-radius: 6px;
            text-align: center;
            font-size: 1.1em;
            margin-bottom: 12px;
        }}
        .formula-explanation {{
            margin: 12px 0;
            line-height: 1.6;
        }}
        .example-box {{
            background: #ecfdf5;
            border-left: 4px solid #10b981;
            border-radius: 8px;
            padding: 12px 16px;
            margin-top: 12px;
        }}
        .terms-box {{
            background: #f0f9ff;
            border-radius: 12px;
            padding: 16px;
            margin: 16px 0;
        }}
        .terms-box h3 {{
            margin-top: 0;
            color: #0c4a6e;
        }}
        .term-table {{
            width: 100%;
            border-collapse: collapse;
            margin: 12px 0;
        }}
        .term-table th, .term-table td {{
            border: 1px solid #e2e8f0;
            padding: 12px;
            text-align: left;
        }}
        .term-table th {{
            background: #f1f5f9;
            color: #1e293b;
        }}
        .term-table tr:nth-child(even) {{
            background: #fafafa;
        }}
    </style>
</head>
<body>
    <h1>📚 {safe_title}</h1>
    <div class="meta">
        <strong>{style_label}</strong><br>
        Source PDF: {safe_pdf}<br>
        Model: {safe_model}<br>
        Generated at (UTC): {html.escape(generated_at)}
    </div>
    <div class="toc">
        <h3>📋 目錄</h3>
        <ul>
            {toc_html}
        </ul>
    </div>
    {sections_html}
</body>
</html>"""


def _ensure_list_blank_lines(text: str) -> str:
    """Insert a blank line before markdown list items and table rows not already preceded by one.

    The Python ``markdown`` library (with ``tables`` + ``nl2br`` extensions)
    requires at least one blank line before a list block or GFM table when
    preceded by non-list/non-table text.  This pre-processor guarantees the
    blank line so that lists render as ``<ul>/<ol><li>`` and tables render as
    ``<table>`` instead of literal text with ``<br>`` separators.
    """
    lines = text.split("\n")
    result: List[str] = []
    list_re = re.compile(r"^[ \t]*(?:[-*+]|\d+\.)[ \t]+")
    table_re = re.compile(r"^[ \t]*\|")
    for line in lines:
        prev = result[-1] if result else ""
        is_list = bool(list_re.match(line))
        is_table = bool(table_re.match(line))
        prev_is_list = bool(list_re.match(prev))
        prev_is_table = bool(table_re.match(prev))
        if is_list or is_table:
            # Blank line needed before list/table if previous is non-blank, non-compatible
            if prev.strip() and not prev_is_list and not prev_is_table:
                result.append("")
        elif line.strip() and prev_is_table:
            # Table blocks need a closing blank line; without it the next
            # paragraph text is absorbed as an extra table row.
            result.append("")
        result.append(line)
    return "\n".join(result)


def _text_to_html_blocks(text: str) -> str:
    protected, formulas = _protect_latex(text)
    markdown_input = _inject_display_formula_blocks(protected, formulas)
    markdown_input = _ensure_list_blank_lines(markdown_input)
    rendered = markdown.markdown(
        markdown_input,
        extensions=["tables", "fenced_code", "nl2br"],
    )
    restored = _restore_formula_placeholders(rendered, formulas)
    restored = re.sub(
        r"<p>\s*(<div class=\"formula\">.*?</div>)\s*</p>",
        r"\1",
        restored,
        flags=re.DOTALL,
    )
    return "\n".join(f"            {line}" for line in restored.splitlines())


def _inject_display_formula_blocks(text: str, formulas: List[str]) -> str:
    injected = text
    for idx, formula in enumerate(formulas):
        compact = re.sub(r"\s+", "", formula)
        if (compact.startswith("$$") and compact.endswith("$$")) or (
            compact.startswith("\\[") and compact.endswith("\\]")
        ):
            placeholder = f"{LATEX_PLACEHOLDER}{idx}X"
            injected = re.sub(
                rf"(?m)^[ \t]*{re.escape(placeholder)}[ \t]*$",
                f"\n<div class=\"formula\">{placeholder}</div>\n",
                injected,
            )
    return injected


def _protect_latex(text: str) -> Tuple[str, List[str]]:
    formulas: List[str] = []

    def _capture(match: re.Match[str]) -> str:
        formulas.append(match.group(0))
        return f"{LATEX_PLACEHOLDER}{len(formulas) - 1}X"

    protected = text
    protected = re.sub(r"\\\[(.*?)\\\]", _capture, protected, flags=re.DOTALL)
    protected = re.sub(r"\$\$(.*?)\$\$", _capture, protected, flags=re.DOTALL)
    protected = re.sub(r"\\\((.*?)\\\)", _capture, protected, flags=re.DOTALL)
    protected = re.sub(r"(?<!\$)\$([^\$\n]{1,300}?)\$(?!\$)", _capture, protected)
    return protected, formulas


def _restore_formula_placeholders(text: str, formulas: List[str]) -> str:
    restored = text
    for idx, formula in enumerate(formulas):
        escaped_formula = html.escape(formula)
        if _is_display_formula(formula):
            replacement = escaped_formula
        else:
            replacement = f"<span class=\"math inline\">{escaped_formula}</span>"
        restored = restored.replace(f"{LATEX_PLACEHOLDER}{idx}X", replacement)
    return restored


def _is_display_formula(formula: str) -> bool:
    compact = re.sub(r"\s+", "", formula)
    return (compact.startswith("$$") and compact.endswith("$$")) or (
        compact.startswith("\\[") and compact.endswith("\\]")
    )


def _safe_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def _safe_range_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _normalize_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n"}:
        return False
    return default


def _normalize_rewrite_response_format(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"markdown", "json"}:
        return normalized
    return DEFAULT_REWRITE_RESPONSE_FORMAT


def _merge_notes(first: Optional[str], second: Optional[str]) -> Optional[str]:
    left = str(first or "").strip()
    right = str(second or "").strip()
    if left and right:
        return f"{left}; {right}"
    if left:
        return left
    if right:
        return right
    return None


def _normalize_rewrite_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"paragraph", "chunk"}:
        return mode
    return DEFAULT_REWRITE_MODE


def _split_section_into_rewrite_parts(
    *,
    section_title: str,
    source_text: str,
    max_chunk_chars: int,
    rewrite_mode: str,
) -> List[Dict[str, str]]:
    normalized_mode = _normalize_rewrite_mode(rewrite_mode)
    if normalized_mode == "paragraph":
        chunks = _paragraph_parts_for_rewrite(source_text, max_chunk_chars)
    else:
        chunks = _chunk_text_for_rewrite(source_text, max_chunk_chars)

    if not chunks:
        return [{"title": section_title, "source_text": source_text.strip()}]

    if len(chunks) == 1:
        return [{"title": section_title, "source_text": chunks[0]}]

    total = len(chunks)
    part_label = "paragraph" if normalized_mode == "paragraph" else "part"
    parts: List[Dict[str, str]] = []
    for idx, chunk in enumerate(chunks, start=1):
        parts.append(
            {
                "title": f"{section_title} ({part_label} {idx}/{total})",
                "source_text": chunk,
            }
        )
    return parts


def _paragraph_parts_for_rewrite(source_text: str, max_chunk_chars: int) -> List[str]:
    text = str(source_text or "").strip()
    if not text:
        return []

    max_chars = max(int(max_chunk_chars or DEFAULT_REWRITE_CHUNK_CHARS), 400)
    raw_paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    if not raw_paragraphs:
        raw_paragraphs = [text]

    parts: List[str] = []
    for paragraph in raw_paragraphs:
        if len(paragraph) <= max_chars:
            parts.append(paragraph)
            continue
        parts.extend(_split_long_text(paragraph, max_chars))
    return [part for part in parts if part.strip()]


def _chunk_text_for_rewrite(source_text: str, max_chunk_chars: int) -> List[str]:
    text = str(source_text or "").strip()
    if not text:
        return []

    max_chars = max(int(max_chunk_chars or DEFAULT_REWRITE_CHUNK_CHARS), 400)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks: List[str] = []
    current = ""

    def _append_piece(piece: str) -> None:
        nonlocal current
        candidate = piece.strip()
        if not candidate:
            return
        if not current:
            current = candidate
            return
        joined = f"{current}\n\n{candidate}"
        if len(joined) <= max_chars:
            current = joined
            return
        chunks.append(current)
        current = candidate

    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            _append_piece(paragraph)
            continue
        for piece in _split_long_text(paragraph, max_chars):
            _append_piece(piece)

    if current:
        chunks.append(current)
    return chunks


def _split_long_text(text: str, max_chars: int) -> List[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    sentence_candidates = re.split(r"(?<=[。！？.!?])\s+", normalized)
    sentences = [s.strip() for s in sentence_candidates if s.strip()]
    if len(sentences) <= 1:
        sentences = [normalized]

    pieces: List[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            start = 0
            while start < len(sentence):
                part = sentence[start : start + max_chars].strip()
                if part:
                    pieces.append(part)
                start += max_chars
            continue

        if not current:
            current = sentence
            continue

        candidate = f"{current} {sentence}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            pieces.append(current)
            current = sentence

    if current:
        pieces.append(current)
    return pieces
