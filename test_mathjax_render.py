#!/usr/bin/env python3
"""MathJax smoke test for Q&A rendering.

用法：
    /home/linuxbrew/.linuxbrew/bin/python3 test_mathjax_render.py

輸出：
    /tmp/storyteller_mathjax_smoke.html
"""

from pathlib import Path

from qa_render import answer_to_mathjax_html

SAMPLE_ANSWER = r"""
這是一個 smoke test，固定驗證四種公式分隔符。

## 行內公式

- dollar inline: $x = 1$
- slash inline: \(\alpha + \beta\)
- subscript inline: $L_{soft}$

## 行間公式

$$L = \alpha \cdot L_{soft} + \beta \cdot L_{hard}$$

\[
\mathrm{KL}(P_T \parallel P_S) = \sum_i P_T(i) \log \frac{P_T(i)}{P_S(i)}
\]

## Markdown 測試

**粗體**、表格、以及一般段落都要正常。

| 項目 | 狀態 |
|------|------|
| 行內公式 | 應渲染 |
| 行間公式 | 應渲染 |
""".strip()


def main() -> None:
    html, height = answer_to_mathjax_html(SAMPLE_ANSWER)

    # 基本保護：輸出中應保留數學分隔符，且不應殘留 placeholder。
    assert "LATEXPH" not in html, "placeholder 未被還原"
    assert "$x = 1$" in html or "\\(x = 1\\)" in html
    assert "$$L = \\alpha \\cdot L_{soft} + \\beta \\cdot L_{hard}$$" in html
    assert height >= 260

    out_path = Path("/tmp/storyteller_mathjax_smoke.html")
    out_path.write_text(html, encoding="utf-8")
    print(f"✅ smoke test HTML 已輸出：{out_path}")
    print(f"建議高度：{height}")
    print("可用瀏覽器打開檢查實際渲染結果。")


if __name__ == "__main__":
    main()
