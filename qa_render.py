#!/usr/bin/env python3
"""Q&A 回答渲染工具。

設計目標：
1. 先保護 LaTeX，避免被 Markdown 解析器破壞。
2. 同時支援 `$...$`、`$$...$$`、`\\(...\\)`、`\\[...\\]`。
3. 輸出單一 HTML 文件，供 Streamlit iframe 直接顯示。
"""

from __future__ import annotations

import re
from typing import List, Tuple

try:
    import markdown as _markdown
except Exception:  # pragma: no cover - fallback for minimal environments
    _markdown = None

LATEX_PLACEHOLDER = "LATEXPH"


def strip_thinking_block(answer: str) -> str:
    """移除 DeepSeek-R1 常見的 <think>...</think> 區塊。"""
    return re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()


def protect_latex(answer: str) -> tuple[str, List[Tuple[str, str]]]:
    """把公式替換成佔位符，等 Markdown 轉完再還原。"""
    latex_blocks: List[Tuple[str, str]] = []

    def protect_block(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        latex_blocks.append(("block", inner))
        return f"\n\n{LATEX_PLACEHOLDER}{len(latex_blocks) - 1}\n\n"

    def protect_inline(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        latex_blocks.append(("inline", inner))
        return f"{LATEX_PLACEHOLDER}{len(latex_blocks) - 1}"

    protected = answer
    # 順序很重要：先抓行間，再抓行內，避免互相吞掉。
    protected = re.sub(r"\\\[(.*?)\\\]", protect_block, protected, flags=re.DOTALL)
    protected = re.sub(r"\$\$(.*?)\$\$", protect_block, protected, flags=re.DOTALL)
    protected = re.sub(r"\\\((.*?)\\\)", protect_inline, protected, flags=re.DOTALL)
    protected = re.sub(r"(?<!\$)\$([^\$\n]{1,300}?)\$(?!\$)", protect_inline, protected)
    return protected, latex_blocks


def markdown_to_html(text: str) -> str:
    """把 Markdown 轉成 HTML；缺套件時退回簡易段落模式。"""
    if _markdown is not None:
        return _markdown.markdown(text, extensions=["tables", "fenced_code"])

    paragraphs = text.split("\n\n")
    return "\n".join(f'<p>{p.replace(chr(10), "<br>")}</p>' for p in paragraphs)


def restore_latex(body_html: str, latex_blocks: List[Tuple[str, str]]) -> str:
    """把佔位符還原成 MathJax 可辨識的公式分隔符。"""
    for idx, (kind, inner) in enumerate(latex_blocks):
        if kind == "block":
            replacement = f'</p><div class="math-block">$${inner}$$</div><p>'
        else:
            replacement = f'<span class="math-inline">${inner}$</span>'
        body_html = body_html.replace(f"{LATEX_PLACEHOLDER}{idx}", replacement)

    # 清掉因 block math 還原時可能產生的空段落。
    body_html = body_html.replace("<p></p>", "")
    body_html = body_html.replace("<p>\n</p>", "")
    return body_html


def estimate_answer_height(answer: str) -> int:
    """粗估 iframe 高度，避免回答被截斷。"""
    lines_count = answer.count("\n") + len(answer) // 70 + 8
    return max(260, min(1200, lines_count * 30 + 120))


def build_mathjax_document(body_html: str) -> str:
    """組合成完整 HTML 文件，供 Streamlit iframe 顯示。"""
    return r'''<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script>
window.MathJax = {
  tex: {
    inlineMath: [['$', '$'], ['\\(', '\\)']],
    displayMath: [['$$', '$$'], ['\\[', '\\]']],
    processEscapes: true
  },
  options: {
    skipHtmlTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code']
  },
  startup: {
    ready() {
      MathJax.startup.defaultReady();
      MathJax.startup.promise.then(() => MathJax.typesetPromise());
    }
  }
};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
<style>
* { box-sizing: border-box; }
body { font-family: "Noto Sans TC", sans-serif; line-height: 1.8; padding: 8px 12px; margin: 0; font-size: 14px; }
p { margin: 6px 0; }
.math-block { text-align: center; margin: 16px 0; overflow-x: auto; }
.math-inline { white-space: normal; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; }
th, td { border: 1px solid #e2e8f0; padding: 8px 12px; }
th { background: #f1f5f9; }
code { background: #f1f5f9; padding: 2px 5px; border-radius: 3px; font-size: 12px; }
blockquote { border-left: 3px solid #3b82f6; padding-left: 12px; color: #475569; }
</style>
</head>
<body>''' + body_html + '''</body>
</html>'''


def answer_to_mathjax_html(answer: str) -> tuple[str, int]:
    """把原始回答轉成最終 HTML 與建議高度。

    流程：
    thinking 區塊清理 → LaTeX 保護 → Markdown 轉 HTML → 還原 LaTeX → 組成完整文件。
    """
    cleaned = strip_thinking_block(answer)
    protected, latex_blocks = protect_latex(cleaned)
    body_html = markdown_to_html(protected)
    body_html = restore_latex(body_html, latex_blocks)
    return build_mathjax_document(body_html), estimate_answer_height(cleaned)
