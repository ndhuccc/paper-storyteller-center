#!/usr/bin/env python3
"""
論文說書人中心 - Streamlit GUI

設計原則：
1. GUI 只負責畫面與互動流程。
2. Q&A 回答的 Markdown/LaTeX 渲染集中在 qa_render.py。
3. 搜尋結果以 chunk 命中為基礎，再依 paper_id 去重成論文列表。
"""

import streamlit as st
from pathlib import Path
from typing import Any, Dict, List

# Q&A 的 MathJax/Markdown 渲染集中放在獨立模組，避免 GUI 檔案再度膨脹。
from center_service import answer as service_answer
from center_service import get_all_papers
from center_service import get_generation_job
from center_service import is_paper_ready
from center_service import launch_generation_job
from center_service import load_html as load_paper_html
from center_service import list_generation_jobs
from center_service import normalize_paper
from center_service import rebuild_index as service_rebuild_index
from center_service import resolve_generation_manifest_paper
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
    "podcast": "Podcast（口語化、對話感）",
    "fairy": "童話故事（擬人化、主角/挑戰/勝利結構）",
    "lazy": "懶人包（bullet points、圖像化、快速抓重點）",
    "question": "問題驅動（先問問題、再逐層解釋）",
    "log": "實驗日誌（研究過程記錄、工程師視角）",
}

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


def rebuild_index():
    """重建向量索引。"""
    if service_rebuild_index():
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
        "sections_generated": sections_generated if sections_generated is not None else "-",
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

    pdf_path = st.text_input(
        "PDF 路徑",
        placeholder="例如：/home/user/Documents/paper.pdf",
        key="gen_pdf_path",
    )
    style = st.selectbox(
        "風格",
        options=list(STYLE_LABELS.keys()),
        format_func=lambda key: f"{key} - {STYLE_LABELS.get(key, key)}",
        key="gen_style",
    )
    auto_index = st.checkbox("完成後自動重建索引", value=True, key="gen_auto_index")

    if st.button("🚀 提交生成任務", key="gen_submit_btn", use_container_width=True):
        if not pdf_path.strip():
            st.warning("請先輸入 PDF 路徑")
        else:
            payload = {
                "pdf_path": pdf_path.strip(),
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
            st.write(f"auto_index: {info['auto_index_summary']}")
            st.write(f"auto_index_mode: {info['auto_index_mode']}")
            st.write(f"auto_index_state: {info['auto_index_state_name']}")
            st.write(f"auto_index_started_at: {info['auto_index_started_at']}")
            st.write(f"auto_index_completed_at: {info['auto_index_completed_at']}")
            st.write(f"auto_index_duration_ms: {info['auto_index_duration_ms']}")
            st.write(f"auto_index_manifest: {info['auto_index_manifest_summary']}")
            st.write(f"updated_at: {info['updated_at']}")

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
