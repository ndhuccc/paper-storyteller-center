#!/usr/bin/env python3
"""
論文說書人中心 - Streamlit GUI
功能：論文搜尋、Q&A 對話、論文閱覽
"""

import streamlit as st
import json
import subprocess
from pathlib import Path
from typing import List, Dict
import urllib.request

from qa_render import answer_to_mathjax_html

# ==================== 配置 ====================
STORYTELLERS_DIR = Path.home() / "Documents" / "Storytellers"
LANCEDB_PATH = STORYTELLERS_DIR / "papers.lance"
OLLAMA_BASE_URL = "http://localhost:11434"
EMBEDDING_MODEL = "qwen3-embedding:8b"
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
    .qa-user {
        background: #dbeafe;
        padding: 10px 14px;
        border-radius: 10px 10px 2px 10px;
        margin: 6px 0;
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
    """純向量搜尋：以 chunk 為單位搜尋，再依論文去重。"""
    db = get_lance_db()
    if db is None:
        return []
    
    query_embedding = get_embedding(query)
    if not query_embedding:
        return []
    
    try:
        tbl = db.open_table("papers")
        # 多抓幾個 chunk，去重後再取前 top_k 篇論文
        results = tbl.search(query_embedding, vector_column_name="embedding") \
                     .limit(top_k * 5).to_pandas().to_dict("records")
        
        # similarity_threshold=0.0 表示全部顯示
        results = [r for r in results if (1.0 - r.get('_distance', 9999)) >= similarity_threshold]
        
        # 每篇論文只保留相似度最高的 chunk
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
        results = forced_papers
    else:
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
    """渲染 Q&A 回答，支援 Markdown + LaTeX（行內/行間）。"""
    html_content, height = answer_to_mathjax_html(answer)
    st.components.v1.html(html_content, height=height, scrolling=True)


def load_paper_html(paper_id: str) -> str:
    # chunk id（如 xxx_chunk_0）要先還原成原始論文 id
    if '_chunk_' in paper_id:
        paper_id = paper_id.rsplit('_chunk_', 1)[0]
    for filename in [f"{paper_id}.html"]:
        filepath = STORYTELLERS_DIR / filename
        if filepath.exists():
            content = filepath.read_text(encoding='utf-8')
            # 若原始 HTML 沒帶 MathJax，就補上
            if 'MathJax' not in content and '</head>' in content:
                mathjax = '<script src="https://polyfill.io/v3/polyfill.min.js?features=es6"></script><script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>'
                content = content.replace('</head>', f'{mathjax}</head>')
            
            # 讓 href="#xxx" 在 iframe 內也能正常滾動
            anchor_fix = """
<script>
document.addEventListener('DOMContentLoaded', function() {
    // 攔截 a[href^="#"]，改為 iframe 內部滾動
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
    """取得所有論文，每篇只回傳一筆。"""
    db = get_lance_db()
    if db is None:
        return []
    try:
        tbl = db.open_table("papers")
        all_rows = tbl.to_pandas().to_dict("records")
        # 依 paper_id 去重，只保留第一筆
        seen = {}
        for r in all_rows:
            pid = r.get('paper_id', r.get('id', ''))
            if pid not in seen:
                seen[pid] = r
        return list(seen.values())
    except:
        return []


# ==================== Dialog ====================
@st.dialog("📖 論文閱覽", width="large")
def show_paper_dialog(paper: Dict):
    html_content = load_paper_html(paper.get('paper_id', paper.get('id', '')))
    # 只顯示 HTML 內容本身
    st.components.v1.html(html_content, height=750, scrolling=True)


def init_session_state():
    if "qa_history" not in st.session_state:
        st.session_state.qa_history = []
    if "open_paper" not in st.session_state:
        st.session_state.open_paper = None
    if "selected_papers" not in st.session_state:
        st.session_state.selected_papers = {}


def maybe_show_open_paper_dialog():
    if st.session_state.open_paper is not None:
        show_paper_dialog(st.session_state.open_paper)
        st.session_state.open_paper = None


def rebuild_index():
    result = subprocess.run(
        ["/home/linuxbrew/.linuxbrew/bin/python3", str(STORYTELLERS_DIR / "paper_center.py"), "rebuild"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        st.success("✅ 完成！")
        st.cache_resource.clear()
        st.rerun()
    else:
        st.error(f"❌ {result.stderr[:200]}")


def similarity_badge(result: Dict) -> str:
    dist = result.get("_distance")
    if dist is None:
        return ""

    similarity = 1.0 - dist
    if similarity >= 0.5:
        sim_color = "#22c55e"
    elif similarity >= 0.25:
        sim_color = "#f59e0b"
    else:
        sim_color = "#ef4444"

    return (
        f'<span style="background:{sim_color};color:white;padding:2px 8px;'
        f'border-radius:4px;font-size:12px;">📌 相似度 {similarity:.2f}</span>'
    )


def render_sidebar(all_papers: List[Dict]):
    with st.sidebar:
        st.header("📊 統計")
        st.metric("論文數量", len(all_papers))
        st.divider()

        if st.button("🔄 重建索引", use_container_width=True):
            with st.spinner("重建中..."):
                rebuild_index()

        st.divider()
        st.header("📚 所有論文")
        for paper in all_papers:
            if st.button(f"📄 {paper.get('title', '?')[:28]}...", key=f"sidebar_{paper.get('id')}"):
                st.session_state.open_paper = paper
                st.rerun()


def render_search_result_item(index: int, result: Dict):
    paper_id = result.get("paper_id", result.get("id", ""))
    with st.container(border=True):
        col_chk, col_info, col_btn = st.columns([0.5, 4, 1])
        with col_chk:
            st.write("")
            checked = st.checkbox("", key=f"chk_{index}_{paper_id}", value=paper_id in st.session_state.selected_papers)
            if checked:
                st.session_state.selected_papers[paper_id] = result
            else:
                st.session_state.selected_papers.pop(paper_id, None)

        with col_info:
            st.markdown(f"**{index + 1}. {result.get('title', '未知')}**")
            st.markdown(
                f"📅 {result.get('date', '未知')}　✍️ {result.get('authors', '未知')[:35]}　{similarity_badge(result)}",
                unsafe_allow_html=True,
            )

        with col_btn:
            st.write("")
            if st.button("📖 閱覽", key=f"view_{index}_{result.get('id')}", use_container_width=True):
                st.session_state.open_paper = result
                st.rerun()


def render_search_panel():
    st.header("🔍 語意搜尋")
    query = st.text_input("輸入關鍵字", placeholder="例如：知識蒸餾、深度學習...", key="search_input")

    if not query:
        return

    with st.spinner("搜尋中..."):
        results = search_papers(query)

    if not results:
        st.warning("⚠️ 沒有找到相關論文")
        return

    st.success(f"找到 **{len(results)}** 篇相關論文")
    st.caption("☑ 勾選論文加入 Q&A 範圍（不勾選則自動搜尋）")
    for index, result in enumerate(results):
        render_search_result_item(index, result)


def render_selected_papers_section():
    selected = st.session_state.selected_papers
    if selected:
        st.info(f"📌 Q&A 範圍：{len(selected)} 篇選取的論文")
        for paper_id, paper in selected.items():
            col_tag, col_rm = st.columns([5, 1])
            with col_tag:
                st.markdown(f"・{paper.get('title', '?')[:40]}...")
            with col_rm:
                if st.button("✕", key=f"rm_{paper_id}"):
                    st.session_state.selected_papers.pop(paper_id, None)
                    st.rerun()
    else:
        st.caption("💡 未指定論文，Q&A 將自動搜尋最相關的論文")


def render_qa_history():
    for qa_idx, qa in enumerate(st.session_state.qa_history):
        st.markdown(f'<div class="qa-user">❓ {qa["question"]}</div>', unsafe_allow_html=True)
        with st.container(border=True):
            render_answer(qa["answer"])
        if qa.get("sources"):
            cols = st.columns(len(qa["sources"]))
            for ci, src in enumerate(qa["sources"]):
                with cols[ci]:
                    if st.button(f"📄 {src.get('title', '?')[:20]}...", key=f"qa_src_{qa_idx}_{ci}_{src.get('id', ci)}"):
                        st.session_state.open_paper = src
                        st.rerun()
        st.divider()


def render_qa_input_section():
    question = st.text_input("輸入問題", placeholder="例如：兩篇論文的樣本選取有何不同？", key="qa_input")

    col_submit, col_clear = st.columns([3, 1])
    with col_submit:
        ask_btn = st.button("🚀 送出問題", use_container_width=True)
    with col_clear:
        if st.button("🗑️ 清除", use_container_width=True):
            st.session_state.qa_history = []
            st.rerun()

    if not (ask_btn and question):
        return

    with st.spinner("🤔 思考中..."):
        selected_list = list(st.session_state.selected_papers.values())
        if selected_list:
            answer, sources = answer_question(question, forced_papers=selected_list)
        else:
            answer, sources = answer_question(question)

    st.session_state.qa_history.append({
        "question": question,
        "answer": answer,
        "sources": sources,
    })
    st.rerun()


def render_qa_panel():
    st.header("💬 Q&A 對話")
    render_selected_papers_section()
    render_qa_history()
    render_qa_input_section()


# ==================== 主介面 ====================
def main():
    init_session_state()
    maybe_show_open_paper_dialog()

    st.title("🦞 論文說書人中心")
    st.markdown("*用自然語言搜尋論文、對論文內容提問*")

    all_papers = get_all_papers()
    render_sidebar(all_papers)

    col1, col2 = st.columns([1, 1])
    with col1:
        render_search_panel()
    with col2:
        render_qa_panel()


if __name__ == "__main__":
    main()
