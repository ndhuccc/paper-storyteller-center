#!/usr/bin/env python3
"""
論文說書人中心 - Streamlit GUI

設計原則：
1. GUI 只負責畫面與互動流程。
2. Q&A 回答的 Markdown/LaTeX 渲染集中在 qa_render.py。
3. 搜尋結果以 chunk 命中為基礎，再依 paper_id 去重成論文列表。
"""

import streamlit as st
import json
from typing import List, Dict
import urllib.request

# Q&A 的 MathJax/Markdown 渲染集中放在獨立模組，避免 GUI 檔案再度膨脹。
from qa_render import answer_to_mathjax_html
from html_loader import load_paper_html
from paper_repository import get_all_papers
from retrieval_service import clear_lance_db_cache
from retrieval_service import rebuild_index as service_rebuild_index
from retrieval_service import search_papers as service_search_papers

# ==================== 配置 ====================
OLLAMA_BASE_URL = "http://localhost:11434"
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
def search_papers(query: str, top_k: int = 10, similarity_threshold: float = 0.0) -> List[Dict]:
    """純向量搜尋：以 chunk 為單位搜尋，再依論文去重。"""
    try:
        return service_search_papers(query, top_k=top_k, similarity_threshold=similarity_threshold)
    except Exception as e:
        st.error(f"搜尋錯誤: {e}")
        return []


def answer_question(question: str, forced_papers: List[Dict] = None) -> tuple[str, List[Dict]]:
    """根據指定論文或自動搜尋結果，產生 Q&A 回答。"""
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
    """把回答交給 qa_render.py 轉成可嵌入的 MathJax HTML。"""
    html_content, height = answer_to_mathjax_html(answer)
    st.components.v1.html(html_content, height=height, scrolling=True)


# ==================== Dialog ====================
@st.dialog("📖 論文閱覽", width="large")
def show_paper_dialog(paper: Dict):
    """以原生 Streamlit dialog 顯示論文 HTML。"""
    html_content = load_paper_html(paper.get('paper_id', paper.get('id', '')))
    # 只顯示 HTML 內容本身
    st.components.v1.html(html_content, height=750, scrolling=True)


def init_session_state():
    """初始化本頁會用到的 session state。"""
    if "qa_history" not in st.session_state:
        st.session_state.qa_history = []
    if "open_paper" not in st.session_state:
        st.session_state.open_paper = None
    if "selected_papers" not in st.session_state:
        st.session_state.selected_papers = {}


def maybe_show_open_paper_dialog():
    """若有待開啟論文，就在主流程中觸發 dialog。"""
    if st.session_state.open_paper is not None:
        show_paper_dialog(st.session_state.open_paper)
        st.session_state.open_paper = None


def rebuild_index():
    """重建向量索引。"""
    if service_rebuild_index():
        clear_lance_db_cache()
        st.success("✅ 完成！")
        st.cache_resource.clear()
        st.rerun()
    else:
        st.error("❌ 重建失敗")


def similarity_badge(result: Dict) -> str:
    """把 _distance 轉成對使用者顯示的 similarity badge。"""
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
    """渲染側邊欄：統計、重建索引、論文列表。"""
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
    """渲染單一搜尋結果卡片。"""
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
    """渲染左欄搜尋面板與搜尋結果。"""
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
    """顯示目前被指定為 Q&A 範圍的論文。"""
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
    """渲染 Q&A 歷史與每輪來源按鈕。"""
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
    """渲染提問輸入區，並在送出後寫入對話歷史。"""
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
    """渲染右欄 Q&A 面板。"""
    st.header("💬 Q&A 對話")
    render_selected_papers_section()
    render_qa_history()
    render_qa_input_section()


# ==================== 主介面 ====================
def main():
    """頁面主流程：初始化 → dialog → 標題 → sidebar → 左右兩欄。"""
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
