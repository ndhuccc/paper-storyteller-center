#!/usr/bin/env python3
"""
論文說書人中心 - Streamlit GUI

設計原則：
1. GUI 只負責畫面與互動流程。
2. Q&A 回答的 Markdown/LaTeX 渲染集中在 qa_render.py。
3. 搜尋結果以 chunk 命中為基礎，再依 paper_id 去重成論文列表。
"""

import streamlit as st
import re
import time
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

# Q&A 的 MathJax/Markdown 渲染集中放在獨立模組，避免 GUI 檔案再度膨脹。
from center_service import answer as service_answer
from center_service import cancel_generation_job
from center_service import delete_paper as service_delete_paper
from center_service import get_all_papers
from center_service import get_generation_job
from center_service import is_paper_ready
from center_service import launch_generation_job
from center_service import load_html as load_paper_html
from center_service import list_generation_jobs
from center_service import normalize_paper
from center_service import rebuild_index as service_rebuild_index
from center_service import resolve_generation_manifest_paper
from center_service import retry_generation_job
from center_service import search as service_search
from center_service import submit_generation_job
from paper_repository import PAPER_STATUS_GENERATED_NOT_INDEXED
from paper_repository import PAPER_STATUS_INDEX_ONLY
from paper_repository import PAPER_STATUS_READY
from paper_repository import PAPER_STATUS_UNAVAILABLE
from qa_render import answer_to_mathjax_html

STYLE_LABELS: Dict[str, str] = {
    "storyteller": "說書人（生活化類比，重點在「為什麼」）",
    "blog": "科普部落格（鉤子句 + 段落標題 + 結尾留問題）",
    "professor": "大教授（課堂講義 / 可複習）",
    "fairy": "童話故事（知識童話、角色化、寓意對應）",
    "lazy": "懶人包（結論先行、條列重點、快速吸收）",
    "question": "問題驅動（提問引導、逐層拆解、收束答案）",
    "log": "實驗日誌（研究過程記錄、工程師視角）",
}

STORYTELLERS_DIR = Path.home() / "Documents" / "Storytellers"
UPLOADS_DIR = STORYTELLERS_DIR / "uploads"
MAX_UPLOAD_SIZE_MB = 50
UPLOAD_RETENTION_DAYS = 14
UPLOAD_MAX_FILES = 200

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
        return service_search(query, top_k=top_k, similarity_threshold=similarity_threshold)
    except Exception as e:
        st.error(f"搜尋錯誤: {e}")
        return []


def answer_question(question: str, forced_papers: List[Dict] = None) -> tuple[str, List[Dict]]:
    """根據指定論文或自動搜尋結果，產生 Q&A 回答。"""
    return service_answer(question=question, forced_papers=forced_papers)


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
    if "last_generation_job_id" not in st.session_state:
        st.session_state.last_generation_job_id = None
    if "handoff_prefill_search" not in st.session_state:
        st.session_state.handoff_prefill_search = None
    if "handoff_prefill_question" not in st.session_state:
        st.session_state.handoff_prefill_question = None
    if "handoff_prefill_selected_paper" not in st.session_state:
        st.session_state.handoff_prefill_selected_paper = None


def apply_handoff_prefill_if_any():
    """在 widget 建立前套用 handoff 預填內容。"""
    prefill_search = st.session_state.handoff_prefill_search
    if isinstance(prefill_search, str) and prefill_search.strip():
        st.session_state.search_input = prefill_search.strip()

    prefill_question = st.session_state.handoff_prefill_question
    if isinstance(prefill_question, str) and prefill_question.strip():
        st.session_state.qa_input = prefill_question.strip()

    selected_paper = st.session_state.handoff_prefill_selected_paper
    if isinstance(selected_paper, dict):
        paper_id = str(selected_paper.get("paper_id", selected_paper.get("id", ""))).strip()
        if paper_id:
            st.session_state.selected_papers = {paper_id: selected_paper}

    st.session_state.handoff_prefill_search = None
    st.session_state.handoff_prefill_question = None
    st.session_state.handoff_prefill_selected_paper = None


def maybe_show_open_paper_dialog():
    """若有待開啟論文，就在主流程中觸發 dialog。"""
    if st.session_state.open_paper is not None:
        show_paper_dialog(st.session_state.open_paper)
        st.session_state.open_paper = None


def _cleanup_deleted_paper_state(paper_id: str):
    if not paper_id:
        return
    st.session_state.selected_papers.pop(paper_id, None)

    open_paper = st.session_state.get("open_paper")
    if isinstance(open_paper, dict):
        open_paper_id = str(open_paper.get("paper_id", open_paper.get("id", ""))).strip()
        if open_paper_id == paper_id:
            st.session_state.open_paper = None

    handoff_paper = st.session_state.get("handoff_prefill_selected_paper")
    if isinstance(handoff_paper, dict):
        handoff_paper_id = str(handoff_paper.get("paper_id", handoff_paper.get("id", ""))).strip()
        if handoff_paper_id == paper_id:
            st.session_state.handoff_prefill_selected_paper = None


def rebuild_index():
    """重建向量索引。"""
    if service_rebuild_index():
        st.success("✅ 完成！")
        st.cache_resource.clear()
        st.rerun()
    else:
        st.error("❌ 重建失敗")


def _sanitize_upload_filename(filename: str) -> str:
    name = str(filename or "").strip() or "uploaded.pdf"
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name


def _save_uploaded_pdf(uploaded_file: Any) -> Path:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_upload_filename(getattr(uploaded_file, "name", "uploaded.pdf"))
    target = UPLOADS_DIR / f"{uuid4().hex[:12]}_{safe_name}"
    target.write_bytes(uploaded_file.getbuffer())
    return target.resolve()


def _get_uploaded_file_size(uploaded_file: Any) -> int:
    size = getattr(uploaded_file, "size", None)
    if isinstance(size, int) and size >= 0:
        return size
    try:
        return len(uploaded_file.getbuffer())
    except Exception:
        return -1


def _cleanup_old_uploaded_pdfs(*, keep: List[Path] | None = None) -> Dict[str, int]:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    keep_set = {path.resolve() for path in (keep or [])}
    removed = 0
    failed = 0
    now = time.time()
    cutoff = now - (UPLOAD_RETENTION_DAYS * 24 * 60 * 60)

    files: List[Path] = []
    for file_path in UPLOADS_DIR.glob("*.pdf"):
        if file_path.resolve() in keep_set:
            continue
        files.append(file_path)

    # Remove old files first.
    for file_path in files:
        try:
            if file_path.stat().st_mtime < cutoff:
                file_path.unlink()
                removed += 1
        except Exception:
            failed += 1

    # Enforce max file count by deleting oldest files.
    remaining: List[Path] = []
    for file_path in UPLOADS_DIR.glob("*.pdf"):
        if file_path.resolve() in keep_set:
            continue
        remaining.append(file_path)

    remaining.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    overflow = max(0, len(remaining) - UPLOAD_MAX_FILES)
    for file_path in remaining[-overflow:]:
        try:
            file_path.unlink()
            removed += 1
        except Exception:
            failed += 1

    return {
        "removed": removed,
        "failed": failed,
        "retention_days": UPLOAD_RETENTION_DAYS,
        "max_files": UPLOAD_MAX_FILES,
    }


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


def _paper_status_style(status: str) -> tuple[str, str]:
    normalized = str(status or "").strip() or PAPER_STATUS_UNAVAILABLE
    if normalized == PAPER_STATUS_READY:
        return "✅", "就緒"
    if normalized == PAPER_STATUS_GENERATED_NOT_INDEXED:
        return "🟡", "待索引"
    if normalized == PAPER_STATUS_INDEX_ONLY:
        return "🟠", "僅索引"
    return "⚪", "不可用"


def _paper_status_badge_text(paper: Dict[str, Any]) -> str:
    status = str(paper.get("paper_status", "")).strip() or PAPER_STATUS_UNAVAILABLE
    icon, label = _paper_status_style(status)
    return f"[{icon} {label}]"


def _paper_status_line(paper: Dict[str, Any]) -> str:
    status = str(paper.get("paper_status", "")).strip() or PAPER_STATUS_UNAVAILABLE
    icon, label = _paper_status_style(status)
    return f"{icon} {label} ({status})"


def render_user_manual():
    """渲染側欄使用說明（精簡版）。"""
    with st.expander("❓ 使用說明", expanded=False):
        st.markdown(
            "**1) 產生新說書 HTML**\n"
            "在右欄「🛠️ 生成說書」可直接上傳 PDF（或填路徑）、選風格後按「🚀 提交生成任務」。\n"
            "成功後可用「📖 開啟生成結果 / 🔍 搜尋這篇 / 💬 詢問這篇」快速回流。\n\n"
            "**2) 論文狀態代表什麼**\n"
            "- `✅ 就緒 (ready)`：可直接搜尋與 Q&A\n"
            "- `🟡 待索引 (generated_not_indexed)`：已有 HTML，尚未完成索引\n"
            "- `🟠 僅索引 (index_only)`：可檢索但缺少 HTML 閱覽檔\n"
            "- `⚪ 不可用 (unavailable)`：資料不足，需先生成或重建索引\n\n"
            "**3) 搜尋與 Q&A**\n"
            "左欄先用關鍵字搜尋；可勾選論文縮小 Q&A 範圍。未勾選時，Q&A 會自動搜尋最相關內容。\n\n"
            "**4) 管理與刪除**\n"
            "側欄「🗂️ 管理（刪除論文）」可移除論文，會同時刪掉索引與本地 HTML，且不可復原。"
        )


def render_sidebar(all_papers: List[Dict]):
    """渲染側邊欄：統計、重建索引、論文列表。"""
    with st.sidebar:
        st.header("📊 統計")
        st.metric("論文數量", len(all_papers))
        st.caption("首次使用可先展開下方「❓ 使用說明」。")
        st.divider()

        if st.button("🔄 重建索引", use_container_width=True):
            with st.spinner("重建中..."):
                rebuild_index()

        st.divider()
        st.header("📚 所有論文")
        if not all_papers:
            st.caption("目前沒有論文，可在右欄「🛠️ 生成說書」先提交 PDF。")
        for paper in all_papers:
            normalized = normalize_paper(paper)
            paper_id = str(normalized.get("paper_id", normalized.get("id", ""))).strip()
            button_title = str(normalized.get("title", "?"))[:24]
            badge = _paper_status_badge_text(normalized)
            if st.button(
                f"📄 {badge} {button_title}",
                key=f"sidebar_{paper_id or normalized.get('id')}",
            ):
                st.session_state.open_paper = normalized
                st.rerun()

        st.divider()
        with st.expander("🗂️ 管理（刪除論文）", expanded=False):
            if not all_papers:
                st.caption("目前沒有可管理的論文")
            else:
                normalized_papers = [normalize_paper(paper) for paper in all_papers]
                options: List[str] = []
                label_to_paper: Dict[str, Dict[str, Any]] = {}
                for paper in normalized_papers:
                    paper_id = str(paper.get("paper_id", paper.get("id", ""))).strip()
                    if not paper_id:
                        continue
                    title = str(paper.get("title", "未知標題")).strip() or "未知標題"
                    status_label = _paper_status_badge_text(paper)
                    option_label = f"{status_label} {title[:32]} ({paper_id})"
                    options.append(option_label)
                    label_to_paper[option_label] = paper

                if not options:
                    st.caption("沒有可刪除的論文資料")
                else:
                    selected_label = st.selectbox(
                        "選擇要刪除的論文",
                        options=options,
                        key="manage_delete_paper_select",
                    )
                    selected_paper = label_to_paper.get(selected_label, {})
                    selected_paper_id = str(
                        selected_paper.get("paper_id", selected_paper.get("id", ""))
                    ).strip()
                    selected_title = str(selected_paper.get("title", "未知標題")).strip() or "未知標題"

                    st.warning("刪除後會同時移除 LanceDB 索引與 Storytellers 目錄下的 HTML，且無法復原。")
                    confirm = st.checkbox(
                        f"我確認刪除《{selected_title[:48]}》",
                        key=f"manage_delete_confirm_{selected_paper_id}",
                    )

                    if st.button(
                        "🗑️ 刪除此論文",
                        key=f"manage_delete_btn_{selected_paper_id}",
                        use_container_width=True,
                        disabled=not (selected_paper_id and confirm),
                    ):
                        with st.spinner("刪除中..."):
                            result = service_delete_paper(selected_paper_id)

                        if result.get("ok"):
                            st.success(str(result.get("message", "刪除完成")))
                        else:
                            st.error(str(result.get("message", "刪除失敗")))

                        index_error = str(result.get("index_error", "")).strip()
                        html_error = str(result.get("html_error", "")).strip()
                        if index_error:
                            st.caption(f"index_error: {index_error}")
                        if html_error:
                            st.caption(f"html_error: {html_error}")

                        _cleanup_deleted_paper_state(selected_paper_id)
                        st.cache_resource.clear()
                        st.rerun()

        st.divider()
        render_user_manual()


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


def _citation_section_label(src: Dict[str, Any], citation: Dict[str, Any]) -> str:
    for key in ("section", "section_title", "section_name", "heading"):
        value = str(citation.get(key, src.get(key, ""))).strip()
        if value:
            return value
    return ""


def _citation_chunk_index(src: Dict[str, Any], citation: Dict[str, Any]) -> Any:
    value = citation.get("chunk_index", src.get("chunk_index"))
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _render_qa_citations(sources: List[Dict[str, Any]]):
    with st.expander("🔎 本輪引用來源", expanded=False):
        for ci, src in enumerate(sources, start=1):
            citation = src.get("citation") if isinstance(src.get("citation"), dict) else {}
            title = str(src.get("title", citation.get("title", "未知"))).strip() or "未知"
            paper_id = str(citation.get("paper_id", src.get("paper_id", src.get("id", "")))).strip()
            section = _citation_section_label(src, citation)
            chunk_index = _citation_chunk_index(src, citation)
            snippet = _short_text(
                citation.get("chunk_snippet", src.get("chunk_text", "")),
                max_len=120,
            )
            similarity = citation.get("similarity")

            meta_parts = []
            if paper_id:
                meta_parts.append(f"paper_id={paper_id}")
            if chunk_index is not None:
                meta_parts.append(f"chunk={chunk_index}")
            if section:
                meta_parts.append(f"section={_short_text(section, max_len=48)}")
            if isinstance(similarity, (int, float)):
                meta_parts.append(f"sim={float(similarity):.2f}")

            st.markdown(f"{ci}. **{_short_text(title, max_len=72)}**")
            if meta_parts:
                st.caption(" | ".join(meta_parts))
            if snippet != "-":
                st.caption(f"片段：{snippet}")


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
            _render_qa_citations(qa["sources"])
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


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return False


def _short_text(text: Any, max_len: int = 80) -> str:
    value = str(text or "").strip()
    if not value:
        return "-"
    if len(value) <= max_len:
        return value
    return f"{value[: max_len - 3]}..."


def _extract_warnings(result: Dict[str, Any]) -> List[str]:
    raw = result.get("warnings")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if raw is None:
        return []
    warning = str(raw).strip()
    return [warning] if warning else []


def _error_to_text(item: Any) -> str:
    if isinstance(item, dict):
        stage = str(item.get("stage", "")).strip()
        err_type = str(item.get("type", "")).strip()
        message = str(item.get("message", "")).strip()
        prefix_parts = [part for part in [stage, err_type] if part]
        prefix = ":".join(prefix_parts)
        if prefix and message:
            return f"{prefix} - {message}"
        if prefix:
            return prefix
        return message
    return str(item).strip()


def _extract_errors(result: Dict[str, Any]) -> List[str]:
    raw = result.get("errors")
    if isinstance(raw, list):
        return [text for text in (_error_to_text(item) for item in raw) if text]
    if raw is None:
        return []
    text = _error_to_text(raw)
    return [text] if text else []


def _step_to_text(step: Any) -> str:
    if isinstance(step, dict):
        name = str(step.get("name", step.get("step", step.get("stage", "step")))).strip() or "step"
        status = str(step.get("status", "")).strip()
        note = str(step.get("note", "")).strip()
        if status and note:
            return f"{name} ({status}) - {note}"
        if status:
            return f"{name} ({status})"
        if note:
            return f"{name} - {note}"
        return name
    return str(step).strip()


def _extract_steps(result: Dict[str, Any]) -> List[str]:
    steps = result.get("steps")
    if not isinstance(steps, list):
        steps = _as_dict(result.get("metadata")).get("steps", [])
    if not isinstance(steps, list):
        return []
    return [text for text in (_step_to_text(step) for step in steps) if text]


def _summarize_items(items: List[str], max_items: int = 2, max_len: int = 96) -> str:
    if not items:
        return "-"
    preview = items[:max_items]
    summary = "; ".join(_short_text(item, max_len=max_len) for item in preview)
    extra = len(items) - len(preview)
    if extra > 0:
        return f"{summary}; +{extra} more"
    return summary


def _auto_index_summary(result: Dict[str, Any], payload: Dict[str, Any]) -> str:
    state = _auto_index_state(result=result, payload=payload)
    requested = state["requested"]
    mode = str(state.get("mode", "")).strip() or "full_rebuild"
    state_name = str(state.get("state", "")).strip()
    if not state_name:
        state_name = "not_requested" if not requested else "requested"
    if not requested:
        return f"{mode}/{state_name}"

    message = str(state.get("message", "")).strip()
    summary = f"{mode}/{state_name}"
    if message:
        return f"{summary}: {message}"
    return summary


def _auto_index_state(result: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _as_dict(result.get("metadata"))
    auto_index = _as_dict(metadata.get("auto_index"))
    requested = auto_index.get("requested")
    if requested is None:
        requested = _as_bool(payload.get("auto_index"))
    attempted = auto_index.get("attempted")
    ok = auto_index.get("ok")
    message = str(auto_index.get("message", "")).strip()
    mode = str(auto_index.get("mode", "")).strip() or "full_rebuild"
    state_name = str(auto_index.get("state", "")).strip()
    started_at = str(auto_index.get("started_at", "")).strip()
    completed_at = str(auto_index.get("completed_at", "")).strip()
    duration_ms = auto_index.get("duration_ms")
    manifest_resolution = _as_dict(auto_index.get("manifest_resolution"))
    manifest_requested = _as_dict(manifest_resolution.get("requested"))
    manifest_paper = _as_dict(manifest_resolution.get("paper"))

    return {
        "requested": _as_bool(requested),
        "attempted": _as_bool(attempted),
        "ok": ok,
        "message": message,
        "mode": mode,
        "state": state_name,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_ms": duration_ms,
        "manifest_resolution": {
            "attempted": _as_bool(manifest_resolution.get("attempted")),
            "ok": manifest_resolution.get("ok"),
            "message": str(manifest_resolution.get("message", "")).strip(),
            "resolved_paper_id": str(manifest_resolution.get("resolved_paper_id", "")).strip(),
            "match_rule": str(manifest_resolution.get("match_rule", "")).strip(),
            "requested": {
                "paper_id": str(manifest_requested.get("paper_id", "")).strip(),
                "output_path": str(manifest_requested.get("output_path", "")).strip(),
                "filename": str(manifest_requested.get("filename", "")).strip(),
            },
            "paper": manifest_paper,
        },
    }


def _first_non_empty_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _extract_generation_output_filename(result: Dict[str, Any], output: Dict[str, Any]) -> str:
    direct = _first_non_empty_text(output.get("filename"), result.get("filename"))
    if direct:
        return Path(direct).name

    artifacts = result.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            if str(artifact.get("type", "")).strip().lower() != "html":
                continue
            filename = _first_non_empty_text(artifact.get("filename"))
            if filename:
                return Path(filename).name
            artifact_path = _first_non_empty_text(artifact.get("path"))
            if artifact_path:
                return Path(artifact_path).name
    return ""


def _extract_generation_output_paper_id(
    payload: Dict[str, Any],
    result: Dict[str, Any],
    output: Dict[str, Any],
) -> str:
    metadata = _as_dict(result.get("metadata"))
    auto_index = _as_dict(metadata.get("auto_index"))
    manifest_resolution = _as_dict(auto_index.get("manifest_resolution"))
    manifest_paper = _as_dict(manifest_resolution.get("paper"))
    return _first_non_empty_text(
        result.get("paper_id"),
        output.get("paper_id"),
        manifest_resolution.get("resolved_paper_id"),
        manifest_paper.get("paper_id"),
        metadata.get("paper_id"),
        payload.get("paper_id"),
    )


def _output_path_exists(output_path: Any) -> bool:
    raw = str(output_path or "").strip()
    if not raw or raw == "-":
        return False
    try:
        return Path(raw).expanduser().exists()
    except Exception:
        return False


def _sanitize_title_for_prefill(title: Any, fallback: str) -> str:
    value = str(title or "").strip()
    if not value:
        value = fallback
    suffix = " - 說書人版"
    if value.endswith(suffix):
        return value[: -len(suffix)].strip() or fallback
    return value


def _auto_index_manifest_summary(auto_index_state: Dict[str, Any]) -> str:
    resolution = _as_dict(auto_index_state.get("manifest_resolution"))
    attempted = _as_bool(resolution.get("attempted"))
    ok = resolution.get("ok")
    message = str(resolution.get("message", "")).strip()
    resolved_paper_id = str(resolution.get("resolved_paper_id", "")).strip()
    match_rule = str(resolution.get("match_rule", "")).strip()
    paper = _as_dict(resolution.get("paper"))
    paper_status = str(paper.get("paper_status", "")).strip()

    if not attempted:
        return message or "not attempted"
    if ok is True:
        detail = resolved_paper_id or "-"
        if paper_status:
            detail = f"{detail} ({paper_status})"
        if match_rule:
            return f"resolved by {match_rule}: {detail}"
        return f"resolved: {detail}"
    return message or "not resolved"


def _build_generation_job_summary(job: Dict[str, Any]) -> Dict[str, Any]:
    payload = _as_dict(job.get("payload"))
    result = _as_dict(job.get("result"))
    output = _as_dict(result.get("output"))
    auto_index_state = _auto_index_state(result=result, payload=payload)

    warnings = _extract_warnings(result)
    errors = _extract_errors(result)
    steps = _extract_steps(result)

    sections_generated = result.get("sections_generated")
    if sections_generated is None:
        sections_generated = _as_dict(result.get("metadata")).get("sections_generated")

    rewrite_model = result.get("model") or _as_dict(result.get("metadata")).get("model") or "-"
    pdf_extraction_model = (
        result.get("pdf_extraction_model")
        or _as_dict(result.get("metadata")).get("pdf_extraction_model")
        or "-"
    )

    output_path = result.get("output_path") or output.get("output_path")
    output_filename = _extract_generation_output_filename(result=result, output=output)
    output_paper_id = _extract_generation_output_paper_id(payload=payload, result=result, output=output)

    return {
        "job_id_full": str(job.get("job_id", "")).strip(),
        "job_id": str(job.get("job_id", ""))[:8],
        "status": str(job.get("status", "-")),
        "pdf_path": str(payload.get("pdf_path", payload.get("source_pdf_path", "-"))),
        "output_path": str(output_path or "-"),
        "output_filename": output_filename,
        "output_paper_id": output_paper_id,
        "sections_generated": str(sections_generated) if sections_generated is not None else "-",
        "rewrite_model": rewrite_model,
        "pdf_extraction_model": pdf_extraction_model,
        "warnings": warnings,
        "warnings_count": len(warnings),
        "warnings_summary": _summarize_items(warnings),
        "errors": errors,
        "errors_count": len(errors),
        "errors_summary": _summarize_items(errors),
        "auto_index_state": auto_index_state,
        "auto_index_mode": str(auto_index_state.get("mode", "")).strip() or "full_rebuild",
        "auto_index_state_name": str(auto_index_state.get("state", "")).strip() or "-",
        "auto_index_started_at": str(auto_index_state.get("started_at", "")).strip() or "-",
        "auto_index_completed_at": str(auto_index_state.get("completed_at", "")).strip() or "-",
        "auto_index_duration_ms": auto_index_state.get("duration_ms"),
        "auto_index_manifest_summary": _auto_index_manifest_summary(auto_index_state),
        "auto_index_summary": _auto_index_summary(result=result, payload=payload),
        "steps": steps,
        "steps_summary": _summarize_items(steps, max_items=3),
        "updated_at": str(job.get("updated_at", "-")),
    }


def _build_generation_rows(job_summaries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把 generation jobs 轉成簡單表格列。"""
    rows: List[Dict[str, Any]] = []
    for info in job_summaries:
        rows.append(
            {
                "job_id": info["job_id"],
                "status": info["status"],
                "output_path": _short_text(info["output_path"], max_len=70),
                "sections_generated": info["sections_generated"],
                "warnings": f'{info["warnings_count"]} | {_short_text(info["warnings_summary"], max_len=72)}',
                "errors": f'{info["errors_count"]} | {_short_text(info["errors_summary"], max_len=72)}',
                "auto_index": _short_text(info["auto_index_summary"], max_len=72),
                "recent_steps": _short_text(info["steps_summary"], max_len=90),
                "updated_at": info["updated_at"],
            }
        )
    return rows


def render_generation_panel(all_papers: List[Dict[str, Any]]):
    """渲染最小可用 generation 面板。"""
    st.divider()
    st.header("🛠️ 生成說書")

    uploaded_pdf = st.file_uploader(
        "上傳 PDF",
        type=["pdf"],
        key="gen_pdf_upload",
        help=f"建議直接上傳（上限 {MAX_UPLOAD_SIZE_MB}MB）；若未上傳，才會使用下方 PDF 路徑。",
    )
    pdf_path = st.text_input(
        "PDF 路徑（可選）",
        placeholder="例如：/home/user/Documents/paper.pdf",
        key="gen_pdf_path",
    )
    style = st.selectbox(
        "說書風格",
        options=list(STYLE_LABELS.keys()),
        format_func=lambda key: f"{key} - {STYLE_LABELS.get(key, key)}",
        key="gen_style",
    )
    auto_index = st.checkbox("完成後自動重建索引", value=True, key="gen_auto_index")

    if st.button("🚀 提交生成任務", key="gen_submit_btn", use_container_width=True):
        resolved_pdf_path = ""
        cleanup_result: Dict[str, int] | None = None
        try:
            if uploaded_pdf is not None:
                size_bytes = _get_uploaded_file_size(uploaded_pdf)
                max_bytes = MAX_UPLOAD_SIZE_MB * 1024 * 1024
                if size_bytes < 0:
                    raise RuntimeError("無法判斷上傳檔案大小")
                if size_bytes > max_bytes:
                    raise ValueError(
                        f"檔案過大（{size_bytes / (1024 * 1024):.1f}MB），"
                        f"目前上限為 {MAX_UPLOAD_SIZE_MB}MB"
                    )
                saved_path = _save_uploaded_pdf(uploaded_pdf)
                resolved_pdf_path = str(saved_path)
                cleanup_result = _cleanup_old_uploaded_pdfs(keep=[saved_path])
            else:
                resolved_pdf_path = pdf_path.strip()
        except Exception as exc:
            st.error(f"PDF 上傳失敗: {exc}")
            return

        if not resolved_pdf_path:
            st.warning("請先上傳 PDF，或輸入有效的 PDF 路徑")
        else:
            payload = {
                "pdf_path": resolved_pdf_path,
                "style": style,
                "auto_index": auto_index,
            }
            try:
                with st.spinner("提交生成任務中..."):
                    job = submit_generation_job(payload=payload)
                    job_id = str(job.get("job_id", "")).strip()
                    if not job_id:
                        raise RuntimeError("job_id is missing in submit response")
                    launched = launch_generation_job(job_id)
                    if launched is None:
                        raise RuntimeError("failed to launch background generation job")
            except Exception as e:
                st.error(f"生成任務失敗: {e}")
            else:
                st.session_state.last_generation_job_id = job_id
                if uploaded_pdf is not None:
                    st.caption(f"已保存上傳檔案：{resolved_pdf_path}")
                if cleanup_result is not None:
                    st.caption(
                        "uploads 清理："
                        f"removed={cleanup_result.get('removed', 0)}, "
                        f"failed={cleanup_result.get('failed', 0)}, "
                        f"retention_days={cleanup_result.get('retention_days', UPLOAD_RETENTION_DAYS)}, "
                        f"max_files={cleanup_result.get('max_files', UPLOAD_MAX_FILES)}"
                    )
                st.success(f"✅ 任務已提交：{job_id[:8]}（背景執行中）")
                st.caption("請在下方任務列表追蹤狀態。")
                st.rerun()

    st.caption("最近任務（最多 8 筆）")
    try:
        recent_jobs = list_generation_jobs(limit=8)
    except Exception as e:
        st.error(f"讀取任務列表失敗: {e}")
        return

    if not recent_jobs:
        st.caption("目前沒有生成任務")
        return

    status_counts: Dict[str, int] = {}
    for job in recent_jobs:
        status = str(job.get("status", "unknown"))
        status_counts[status] = status_counts.get(status, 0) + 1
    summary = " | ".join([f"{k}: {v}" for k, v in status_counts.items()])
    st.caption(f"狀態統計：{summary}")
    job_summaries = [_build_generation_job_summary(job) for job in recent_jobs]
    st.dataframe(_build_generation_rows(job_summaries), use_container_width=True)

    st.caption("點開可查看每筆任務細節")
    for info in job_summaries:
        expander_label = (
            f"{info['job_id']} | {info['status']} | "
            f"sections {info['sections_generated']} | "
            f"warn {info['warnings_count']} | err {info['errors_count']}"
        )
        with st.expander(expander_label, expanded=False):
            st.write(f"status: {info['status']}")
            st.write(f"pdf_path: {info['pdf_path']}")
            st.write(f"output_path: {info['output_path']}")
            st.write(f"sections_generated: {info['sections_generated']}")
            st.write(f"pdf_extraction_model: {info['pdf_extraction_model']}")
            st.write(f"rewrite_model: {info['rewrite_model']}")
            st.write(f"auto_index: {info['auto_index_summary']}")
            st.write(f"auto_index_mode: {info['auto_index_mode']}")
            st.write(f"auto_index_state: {info['auto_index_state_name']}")
            st.write(f"auto_index_started_at: {info['auto_index_started_at']}")
            st.write(f"auto_index_completed_at: {info['auto_index_completed_at']}")
            st.write(f"auto_index_duration_ms: {info['auto_index_duration_ms']}")
            st.write(f"auto_index_manifest: {info['auto_index_manifest_summary']}")
            st.write(f"updated_at: {info['updated_at']}")

            job_id_full = info["job_id_full"]
            status = str(info["status"]).strip().lower()
            if status in {"failed", "succeeded", "canceled"}:
                if st.button(
                    "🔄 重試任務",
                    key=f"retry_job_{job_id_full}",
                    use_container_width=True,
                ):
                    try:
                        retried = retry_generation_job(job_id_full)
                        if retried is None:
                            raise RuntimeError("retry request returned None")
                    except Exception as e:
                        st.error(f"重試任務失敗: {e}")
                    else:
                        st.success("已送出重試，任務將於背景執行。")
                        st.rerun()

            if status in {"pending", "running"}:
                if st.button(
                    "🛑 取消任務",
                    key=f"cancel_job_{job_id_full}",
                    use_container_width=True,
                ):
                    try:
                        canceled = cancel_generation_job(job_id_full)
                        if canceled is None:
                            raise RuntimeError("cancel request returned None")
                    except Exception as e:
                        st.error(f"取消任務失敗: {e}")
                    else:
                        st.success("任務已標記為取消。")
                        st.rerun()

            if info["warnings"]:
                st.write(f"warnings ({info['warnings_count']}):")
                for warning in info["warnings"]:
                    st.write(f"- {warning}")
            else:
                st.write("warnings: 0")

            if info["errors"]:
                st.write(f"errors ({info['errors_count']}):")
                for error in info["errors"]:
                    st.write(f"- {error}")
            else:
                st.write("errors: 0")

            if info["steps"]:
                st.write("recent_steps:")
                for step in info["steps"][-5:]:
                    st.write(f"- {step}")
            else:
                st.write("recent_steps: -")

            if info["status"] != "succeeded":
                continue

            st.write("next_steps:")
            auto_index_state = info["auto_index_state"]
            auto_index_requested = auto_index_state.get("requested") is True
            auto_index_ok = auto_index_state.get("ok") is True
            auto_index_mode = str(auto_index_state.get("mode", "")).strip() or "full_rebuild"

            if auto_index_ok:
                st.success(f"✅ 自動索引流程已完成（mode={auto_index_mode}）。")
            elif auto_index_requested:
                st.warning(
                    f"⚠️ 說書已生成，但自動索引失敗（mode={auto_index_mode}）。"
                    "若要搜尋或 Q&A，請先按側欄「🔄 重建索引」。"
                )
            else:
                st.info(
                    f"ℹ️ 說書已生成，但這次未執行自動索引（mode={auto_index_mode}）。"
                    "若要搜尋或 Q&A，請先按側欄「🔄 重建索引」。"
                )

            output_path = str(info.get("output_path", "")).strip()
            output_exists = _output_path_exists(output_path)
            resolution = resolve_generation_manifest_paper(
                output_path=output_path,
                filename=info.get("output_filename", ""),
                paper_id=info.get("output_paper_id", ""),
                manifest_papers=all_papers,
            )
            manifest_paper = resolution.get("paper") if isinstance(resolution, dict) else None
            if isinstance(manifest_paper, dict):
                manifest_paper = normalize_paper(manifest_paper)
            resolved_paper_id = str((resolution or {}).get("resolved_paper_id", "")).strip()
            open_target_paper_id = str(
                (manifest_paper or {}).get("paper_id", (manifest_paper or {}).get("id", ""))
            ).strip() or resolved_paper_id
            paper_status = str((manifest_paper or {}).get("paper_status", PAPER_STATUS_UNAVAILABLE)).strip() or PAPER_STATUS_UNAVAILABLE
            title_fallback = open_target_paper_id or "這篇論文"
            display_title = _sanitize_title_for_prefill(
                manifest_paper.get("title") if manifest_paper else "",
                fallback=title_fallback,
            )
            paper_ready = bool(manifest_paper) and is_paper_ready(manifest_paper)
            can_handoff_to_search_qa = paper_ready
            can_open_output = bool(open_target_paper_id) and (output_exists or bool(manifest_paper and manifest_paper.get("has_html")))
            st.write(f"paper_status: {_paper_status_line(manifest_paper or {'paper_status': paper_status})}")

            col_open, col_search, col_ask = st.columns([1, 1, 1])
            with col_open:
                if st.button(
                    "📖 開啟生成結果",
                    key=f"handoff_open_{info['job_id_full']}",
                    use_container_width=True,
                    disabled=not can_open_output,
                ):
                    if manifest_paper:
                        st.session_state.open_paper = manifest_paper
                    else:
                        st.session_state.open_paper = {
                            "paper_id": open_target_paper_id,
                            "id": open_target_paper_id,
                            "title": display_title,
                        }
                    st.rerun()

            with col_search:
                if st.button(
                    "🔍 搜尋這篇",
                    key=f"handoff_search_{info['job_id_full']}",
                    use_container_width=True,
                    disabled=not can_handoff_to_search_qa,
                ):
                    st.session_state.handoff_prefill_search = display_title
                    st.rerun()

            with col_ask:
                if st.button(
                    "💬 詢問這篇",
                    key=f"handoff_ask_{info['job_id_full']}",
                    use_container_width=True,
                    disabled=not can_handoff_to_search_qa,
                ):
                    st.session_state.handoff_prefill_selected_paper = manifest_paper
                    st.session_state.handoff_prefill_question = (
                        f"請幫我整理《{display_title}》的核心貢獻、方法、實驗結果與限制。"
                    )
                    st.rerun()

            if paper_ready:
                st.success("✅ 這篇內容在 manifest 中已是「就緒」狀態，可直接用於搜尋/Q&A。")
            elif not output_exists and not (manifest_paper and manifest_paper.get("has_html")):
                st.caption("找不到輸出 HTML，請先確認 output_path 是否存在。")
            else:
                if manifest_paper is None:
                    st.caption("尚未在 paper manifest 找到這篇輸出內容，請稍後刷新或重建索引。")
                else:
                    st.caption(
                        f"目前狀態為 {_paper_status_line(manifest_paper)}，尚未可用於搜尋/Q&A。請先重建索引後再使用「搜尋這篇 / 詢問這篇」。"
                    )

    last_job_id = st.session_state.last_generation_job_id
    if last_job_id:
        latest = get_generation_job(last_job_id)
        if latest:
            st.caption(f"最近提交任務：{str(last_job_id)[:8]}（{latest.get('status', 'unknown')}）")


def render_qa_panel(all_papers: List[Dict[str, Any]]):
    """渲染右欄 Q&A 面板。"""
    st.header("💬 Q&A 對話")
    render_selected_papers_section()
    render_qa_history()
    render_qa_input_section()
    render_generation_panel(all_papers)


# ==================== 主介面 ====================
def main():
    """頁面主流程：初始化 → dialog → 標題 → sidebar → 左右兩欄。"""
    init_session_state()
    apply_handoff_prefill_if_any()
    maybe_show_open_paper_dialog()

    st.title("🦞 論文說書人中心")
    st.markdown("*用自然語言搜尋論文、對論文內容提問*")

    all_papers = get_all_papers()
    render_sidebar(all_papers)

    col1, col2 = st.columns([1, 1])
    with col1:
        render_search_panel()
    with col2:
        render_qa_panel(all_papers)


if __name__ == "__main__":
    main()
