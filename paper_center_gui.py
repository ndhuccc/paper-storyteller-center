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
LLM_MODEL = "qwen3:8b"

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
    data = {"model": EMBEDDING_MODEL, "prompt": text[:1500]}
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read()).get('embedding', [])
    except Exception as e:
        st.error(f"Embedding 錯誤: {e}")
        return []


def search_papers(query: str, top_k: int = 10) -> List[Dict]:
    """混合搜尋：先做關鍵字文字比對，再用向量搜尋排序"""
    db = get_lance_db()
    if db is None:
        return []
    
    try:
        tbl = db.open_table("papers")
        all_papers = tbl.to_list()
    except Exception as e:
        st.error(f"搜尋錯誤: {e}")
        return []
    
    # 第一階段：關鍵字文字比對（主要過濾）
    query_lower = query.lower()
    query_terms = [t.strip() for t in re.split(r'[\s,，、]+', query) if t.strip()]
    
    def text_score(paper: Dict) -> float:
        text = f"{paper.get('title','')}{paper.get('content','')}".lower()
        score = 0.0
        for term in query_terms:
            term_lower = term.lower()
            # 標題命中得更高分
            if term_lower in paper.get('title','').lower():
                score += 5.0
            if term_lower in paper.get('content','').lower():
                score += 1.0
        return score
    
    # 計算文字分數
    scored = []
    for p in all_papers:
        ts = text_score(p)
        if ts > 0:
            scored.append((ts, p))
    
    # 如果文字匹配有結果就用文字分數排序
    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:top_k]]
    
    # 如果文字匹配無結果，試用向量搜尋（加身距離門榕）
    embedding = get_embedding(query)
    if embedding:
        try:
            results = tbl.search(embedding, vector_column_name="embedding").limit(top_k).to_list()
            # 只返回 distance < 450 的結果（關聯度門榕）
            return [r for r in results if r.get('_distance', 9999) < 0.75]
        except:
            pass
    
    return []


def answer_question(question: str) -> tuple[str, List[Dict]]:
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


def load_paper_html(paper_id: str) -> str:
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
    db = get_lance_db()
    if db is None:
        return []
    try:
        tbl = db.open_table("papers")
        return tbl.to_list()
    except:
        return []


# ==================== Dialog（Pop-up Modal）====================
@st.dialog("📖 論文閱覽", width="large")
def show_paper_dialog(paper: Dict):
    html_content = load_paper_html(paper.get('id', ''))
    # 直接顯示 HTML 渲染結果，不加任何標頭
    st.components.v1.html(html_content, height=750, scrolling=True)


# ==================== 主介面 ====================
def main():
    # Session State 初始化
    if "qa_history" not in st.session_state:
        st.session_state.qa_history = []
    if "open_paper" not in st.session_state:
        st.session_state.open_paper = None

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
                for i, r in enumerate(results):
                    with st.container(border=True):
                        col_info, col_btn = st.columns([4, 1])
                        with col_info:
                            st.markdown(f"**{i+1}. {r.get('title','未知')}**")
                            st.caption(f"📅 {r.get('date','未知')}　✍️ {r.get('authors','未知')[:40]}")
                        with col_btn:
                            st.write("")  # 垂直對齊
                            if st.button("📖 閱覽", key=f"view_{i}_{r.get('id')}", use_container_width=True):
                                st.session_state.open_paper = r
                                st.rerun()
            else:
                st.warning("⚠️ 沒有找到相關論文")

    # ── 右欄：Q&A ──────────────────────────
    with col2:
        st.header("💬 Q&A 對話")

        # 對話歷史
        for qa in st.session_state.qa_history:
            st.markdown(f'<div class="qa-user">❓ {qa["question"]}</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="qa-bot">📝 {qa["answer"]}</div>', unsafe_allow_html=True)
            if qa.get("sources"):
                cols = st.columns(len(qa["sources"]))
                for ci, src in enumerate(qa["sources"]):
                    with cols[ci]:
                        if st.button(f"📄 {src.get('title','?')[:20]}...", key=f"qa_src_{ci}_{src.get('id')}"):
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
                answer, sources = answer_question(question)
            st.session_state.qa_history.append({
                "question": question,
                "answer": answer,
                "sources": sources
            })
            st.rerun()


if __name__ == "__main__":
    main()
