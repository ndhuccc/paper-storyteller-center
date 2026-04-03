#!/usr/bin/env python3
"""
論文說書人中心 - Streamlit GUI
功能：論文搜尋、Q&A 對話、論文閱覽
"""

import streamlit as st
import json
import re
from pathlib import Path
from typing import List, Dict
import urllib.request

# ==================== 配置 ====================
STORYTELLERS_DIR = Path.home() / "Documents" / "Storytellers"
LANCEDB_PATH = STORYTELLERS_DIR / "papers.lance"
OLLAMA_BASE_URL = "http://localhost:11434"
EMBEDDING_MODEL = "qwen3-embedding:8b"  # Qwen3 中文 embedding
LLM_MODEL = "deepseek-r1:8b"

# ==================== 頁面設定 ====================
st.set_page_config(
    page_title="📚 論文說書人中心",
    page_icon="🦞",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    [data-testid="stMain"] { background: #f0f4f8; }
    .paper-item {
        background: white;
        padding: 12px 16px;
        border-radius: 10px;
        margin: 6px 0;
        border-left: 4px solid #3b82f6;
        box-shadow: 0 1px 4px rgba(0,0,0,0.07);
    }
    .qa-user {
        background: #dbeafe;
        padding: 10px 14px;
        border-radius: 10px 10px 2px 10px;
        margin: 6px 0;
    }
    .qa-bot {
        background: white;
        padding: 10px 14px;
        border-radius: 2px 10px 10px 10px;
        margin: 6px 0;
        border-left: 3px solid #10b981;
    }
</style>
""", unsafe_allow_html=True)

# ==================== 函數 ====================
@st.cache_resource
def get_lance_db():
    try:
        import lancedb
        return lancedb.connect(str(LANCEDB_PATH))
    except Exception:
        return None


def get_embedding(text: str) -> List[float]:
    url = f"{OLLAMA_BASE_URL}/api/embeddings"
    data = {"model": EMBEDDING_MODEL, "prompt": text[:3000]}
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read()).get('embedding', [])
    except Exception as e:
        st.error(f"Embedding 錯誤: {e}")
        return []


def search_papers(query: str, top_k: int = 10, similarity_threshold: float = 0.0) -> List[Dict]:
    """純向量搜尋（Chunk Embedding）：搜尋最相關 chunks，依論文去重後返回"""
    db = get_lance_db()
    if db is None:
        return []
    
    query_embedding = get_embedding(query)
    if not query_embedding:
        return []
    
    try:
        tbl = db.open_table("papers")
        # 多抓幾個 chunk，去重後取 top_k 篇論文
        results = tbl.search(query_embedding, vector_column_name="embedding") \
                     .limit(top_k * 5).to_pandas().to_dict("records")
        
        # 過濾餘弦相似度門檻（similarity_threshold=0.0 表示全部顯示）
        results = [r for r in results if (1.0 - r.get('_distance', 9999)) >= similarity_threshold]
        
        # 依論文去重：每篇論文只保留相似度最高的 chunk
        best_per_paper = {}
        for r in results:
            pid = r.get('paper_id', r.get('id', ''))
            sim = 1.0 - r.get('_distance', 9999)
            if pid not in best_per_paper or sim > (1.0 - best_per_paper[pid].get('_distance', 9999)):
                best_per_paper[pid] = r
        
        # 依相似度排序
        sorted_papers = sorted(best_per_paper.values(),
                               key=lambda x: 1.0 - x.get('_distance', 9999),
                               reverse=True)
        return sorted_papers[:top_k]
        
    except Exception as e:
        st.error(f"搜尋錯誤: {e}")
        return []


def answer_question(question: str, forced_papers: List[Dict] = None) -> tuple[str, List[Dict]]:
    if forced_papers:
        # 使用指定論文
        results = forced_papers
    else:
        # 自動搜尋最相關論文
        results = search_papers(question, top_k=3)
    if not results:
        return "抱歉，沒有找到相關論文內容。", []
    context = "\n\n".join([f"=== {r.get('title','未知')} ===\n{r.get('content','')[:3000]}" for r in results])
    prompt = f"""你是專業論文說書人，用繁體中文回答，並引用論文標題。

=== 論文內容 ===
{context}

=== 問題 ===
{question}

回答："""
    try:
        req = urllib.request.Request(
            f"{OLLAMA_BASE_URL}/api/generate",
            data=json.dumps({"model": LLM_MODEL, "prompt": prompt, "stream": False}).encode(),
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.loads(response.read()).get('response', '').strip(), results
    except Exception as e:
        return f"生成錯誤：{e}", results


def render_answer(answer: str):
    """渲染 Q&A 回答，支援 LaTeX 公式（MathJax）+ Markdown"""
    import re as _re, markdown as _md
    
    # 1. 過濾 DeepSeek-R1 的 <think>...</think>
    answer = _re.sub(r'<think>.*?</think>', '', answer, flags=_re.DOTALL).strip()
    
    # 2. 保護 LaTeX 公式（避免 Markdown 解析破壞）
    latex_blocks = []  # list of (kind, inner)
    
    def protect_block(m):
        inner = m.group(1).strip()
        latex_blocks.append(('block', inner))
        return f'\n\nLATEXPH{len(latex_blocks)-1}\n\n'
    
    def protect_inline(m):
        inner = m.group(1)
        latex_blocks.append(('inline', inner))
        return f'LATEXPH{len(latex_blocks)-1}'
    
    protected = _re.sub(r'\$\$(.*?)\$\$', protect_block, answer, flags=_re.DOTALL)
    protected = _re.sub(r'(?<!\$)\$([^\$\n]{1,200}?)\$(?!\$)', protect_inline, protected)
    
    # 3. Markdown 轉 HTML
    try:
        body_html = _md.markdown(protected, extensions=['tables', 'fenced_code'])
    except Exception:
        paras = protected.split('\n\n')
        body_html = '\n'.join(f'<p>{p.replace(chr(10), "<br>")}</p>' for p in paras)
    
    # 4. 還原 LaTeX（區塊公式需先閉合 <p>）
    for i, (kind, inner) in enumerate(latex_blocks):
        if kind == 'block':
            repl = f'</p><div style="text-align:center;margin:16px 0;">\\[{inner}\\]</div><p>'
        else:
            repl = f'\\({inner}\\)'
        body_html = body_html.replace(f'LATEXPH{i}', repl)
    
    # 5. 組合完整 HTML
    html_content = f"""<!DOCTYPE html>
<html>
<head>
<script>
window.MathJax = {{
    tex: {{
        inlineMath: [["\\\\(", "\\\\)"]],
        displayMath: [["\\\\[", "\\\\]"]]
    }},
    startup: {{
        ready() {{
            MathJax.startup.defaultReady();
            MathJax.startup.promise.then(() => MathJax.typesetPromise());
        }}
    }}
}};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
<style>
body {{ font-family: "Noto Sans TC", sans-serif; line-height: 1.8; padding: 8px; margin: 0; font-size: 14px; }}
p {{ margin: 8px 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
th, td {{ border: 1px solid #e2e8f0; padding: 8px 12px; }}
th {{ background: #f1f5f9; }}
code {{ background: #f1f5f9; padding: 2px 6px; border-radius: 4px; }}
</style>
</head>
<body>{body_html}</body>
</html>"""
    
    lines = answer.count('\n') + answer.count('\n\n') * 2 + 3
    height = max(120, min(600, lines * 28 + 60))
    st.components.v1.html(html_content, height=height, scrolling=True)


def estimate_height(text: str) -> int:
    """估算回答高度"""
    lines = text.count('\n') + text.count('<br>') + 3
    chars = len(text)
    return max(200, min(800, lines * 30 + chars // 80 * 24))


def load_paper_html(paper_id: str) -> str:
    # paper_id 可能是 chunk id（如 xxx_chunk_0），取出真正的 paper_id
    if '_chunk_' in paper_id:
        paper_id = paper_id.rsplit('_chunk_', 1)[0]
    for filename in [f"{paper_id}.html"]:
        filepath = STORYTELLERS_DIR / filename
        if filepath.exists():
            content = filepath.read_text(encoding='utf-8')
            # 確保 MathJax 正確加載
            if 'MathJax' not in content and '</head>' in content:
                mathjax = '<script src="https://polyfill.io/v3/polyfill.min.js?features=es6"></script><script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>'
                content = content.replace('</head>', f'{mathjax}</head>')
            
            # 修復錨點連結：讓 href="#xxx" 在 iframe 內正常滾動
            # 加入 JS 攔截器確保錨點在 iframe 內有效
            anchor_fix = """
<script>
document.addEventListener('DOMContentLoaded', function() {
    // 攔截所有 a[href^="#"] 連結，確保在 iframe 內滾動
    document.querySelectorAll('a[href^="#"]').forEach(function(link) {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            var targetId = link.getAttribute('href').substring(1);
            var target = document.getElementById(targetId);
            if (target) {
                target.scrollIntoView({behavior: 'smooth', block: 'start'});
            }
        });
    });
});
</script>
"""
            if '</body>' in content:
                content = content.replace('</body>', f'{anchor_fix}</body>')
            else:
                content += anchor_fix
            return content
    return "<html><body><h1>找不到論文</h1></body></html>"


def get_all_papers() -> List[Dict]:
    """取得所有論文（去重，每篇只返回一筆）"""
    db = get_lance_db()
    if db is None:
        return []
    try:
        tbl = db.open_table("papers")
        all_rows = tbl.to_pandas().to_dict("records")
        # 依 paper_id 去重，只保留每篇論文的第一筆
        seen = {}
        for r in all_rows:
            pid = r.get('paper_id', r.get('id', ''))
            if pid not in seen:
                seen[pid] = r
        return list(seen.values())
    except:
        return []


# ==================== Dialog（Pop-up Modal）====================
@st.dialog("📖 論文閱覽", width="large")
def show_paper_dialog(paper: Dict):
    html_content = load_paper_html(paper.get('paper_id', paper.get('id', '')))
    # 直接顯示 HTML 渲染結果，不加任何標頭
    st.components.v1.html(html_content, height=750, scrolling=True)


# ==================== 主介面 ====================
def main():
    # Session State 初始化
    if "qa_history" not in st.session_state:
        st.session_state.qa_history = []
    if "open_paper" not in st.session_state:
        st.session_state.open_paper = None
    if "selected_papers" not in st.session_state:
        st.session_state.selected_papers = {}  # paper_id -> paper dict

    # 觸發 dialog（必須在主程式流程中）
    if st.session_state.open_paper is not None:
        show_paper_dialog(st.session_state.open_paper)
        st.session_state.open_paper = None

    # 標題
    st.title("🦞 論文說書人中心")
    st.markdown("*用自然語言搜尋論文、對論文內容提問*")

    # ── 側邊欄 ──────────────────────────────
    with st.sidebar:
        st.header("📊 統計")
        all_papers = get_all_papers()
        st.metric("論文數量", len(all_papers))
        st.divider()

        if st.button("🔄 重建索引", use_container_width=True):
            with st.spinner("重建中..."):
                import subprocess
                r = subprocess.run(
                    ["/home/linuxbrew/.linuxbrew/bin/python3",
                     str(STORYTELLERS_DIR / "paper_center.py"), "rebuild"],
                    capture_output=True, text=True
                )
                if r.returncode == 0:
                    st.success("✅ 完成！")
                    st.cache_resource.clear()
                    st.rerun()
                else:
                    st.error(f"❌ {r.stderr[:200]}")

        st.divider()
        st.header("📚 所有論文")
        for p in all_papers:
            if st.button(f"📄 {p.get('title','?')[:28]}...", key=f"sidebar_{p.get('id')}"):
                st.session_state.open_paper = p
                st.rerun()

    # ── 主欄 ──────────────────────────────────
    col1, col2 = st.columns([1, 1])

    # ── 左欄：搜尋 ──────────────────────────
    with col1:
        st.header("🔍 語意搜尋")
        query = st.text_input("輸入關鍵字", placeholder="例如：知識蒸餾、深度學習...", key="search_input")

        if query:
            with st.spinner("搜尋中..."):
                results = search_papers(query)

            if results:
                st.success(f"找到 **{len(results)}** 篇相關論文")
                st.caption("☑ 勾選論文加入 Q&A 範圍（不勾選則自動搜尋）")
                for i, r in enumerate(results):
                    pid = r.get('paper_id', r.get('id', ''))
                    with st.container(border=True):
                        col_chk, col_info, col_btn = st.columns([0.5, 4, 1])
                        with col_chk:
                            st.write("")
                            checked = st.checkbox("", key=f"chk_{i}_{pid}",
                                                  value=pid in st.session_state.selected_papers)
                            if checked:
                                st.session_state.selected_papers[pid] = r
                            else:
                                st.session_state.selected_papers.pop(pid, None)
                        with col_info:
                            st.markdown(f"**{i+1}. {r.get('title','未知')}**")
                            dist = r.get('_distance', None)
                            if dist is not None:
                                similarity = 1.0 - dist
                                if similarity >= 0.5:
                                    sim_color = "#22c55e"
                                elif similarity >= 0.25:
                                    sim_color = "#f59e0b"
                                else:
                                    sim_color = "#ef4444"
                                sim_badge = f'<span style="background:{sim_color};color:white;padding:2px 8px;border-radius:4px;font-size:12px;">📌 相似度 {similarity:.2f}</span>'
                            else:
                                sim_badge = ''
                            st.markdown(f"📅 {r.get('date','未知')}　✍️ {r.get('authors','未知')[:35]}　{sim_badge}", unsafe_allow_html=True)
                        with col_btn:
                            st.write("")
                            if st.button("📖 閱覽", key=f"view_{i}_{r.get('id')}", use_container_width=True):
                                st.session_state.open_paper = r
                                st.rerun()
            else:
                st.warning("⚠️ 沒有找到相關論文")

    # ── 右欄：Q&A ──────────────────────────
    with col2:
        st.header("💬 Q&A 對話")

        # 顯示目前選取的論文
        selected = st.session_state.selected_papers
        if selected:
            st.info(f"📌 Q&A 範圍：{len(selected)} 篇選取的論文")
            for pid, p in selected.items():
                col_tag, col_rm = st.columns([5, 1])
                with col_tag:
                    st.markdown(f"・{p.get('title','?')[:40]}...")
                with col_rm:
                    if st.button("✕", key=f"rm_{pid}"):
                        st.session_state.selected_papers.pop(pid, None)
                        st.rerun()
        else:
            st.caption("💡 未指定論文，Q&A 將自動搜尋最相關的論文")

        # 對話歷史
        for qa_idx, qa in enumerate(st.session_state.qa_history):
            st.markdown(f'<div class="qa-user">❓ {qa["question"]}</div>', unsafe_allow_html=True)
            with st.container(border=True):
                render_answer(qa["answer"])
            if qa.get("sources"):
                cols = st.columns(len(qa["sources"]))
                for ci, src in enumerate(qa["sources"]):
                    with cols[ci]:
                        if st.button(f"📄 {src.get('title','?')[:20]}...", key=f"qa_src_{qa_idx}_{ci}_{src.get('id',ci)}"):
                            st.session_state.open_paper = src
                            st.rerun()
            st.divider()

        # 輸入框
        question = st.text_input("輸入問題", placeholder="例如：兩篇論文的樣本選取有何不同？", key="qa_input")

        c1, c2 = st.columns([3, 1])
        with c1:
            ask_btn = st.button("🚀 送出問題", use_container_width=True)
        with c2:
            if st.button("🗑️ 清除", use_container_width=True):
                st.session_state.qa_history = []
                st.rerun()

        if ask_btn and question:
            with st.spinner("🤔 思考中..."):
                selected_list = list(st.session_state.selected_papers.values())
                if selected_list:
                    # 使用選取的論文作為 context
                    answer, sources = answer_question(question, forced_papers=selected_list)
                else:
                    # 自動搜尋
                    answer, sources = answer_question(question)
            st.session_state.qa_history.append({
                "question": question,
                "answer": answer,
                "sources": sources
            })
            st.rerun()


if __name__ == "__main__":
    main()
