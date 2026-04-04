#!/usr/bin/env python3
"""HTML loading utilities for Paper Storyteller Center."""

from pathlib import Path


STORYTELLERS_DIR = Path.home() / "Documents" / "Storytellers"


def load_paper_html(paper_id: str) -> str:
    """Load paper HTML and inject iframe-safe KaTeX/anchor behavior."""
    # chunk id (e.g. xxx_chunk_0) should be restored to original paper id
    if "_chunk_" in paper_id:
        paper_id = paper_id.rsplit("_chunk_", 1)[0]

    for filename in [f"{paper_id}.html"]:
        filepath = STORYTELLERS_DIR / filename
        if not filepath.exists():
            continue

        content = filepath.read_text(encoding="utf-8")

        # If source HTML does not include KaTeX, inject KaTeX + auto-render.
        # This avoids unstable MathJax loading inside iframe.
        if "katex.min.css" not in content and "</head>" in content:
            katex = """
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"></script>
<script>
document.addEventListener('DOMContentLoaded', function() {
  function renderKatexWhenReady() {
    if (typeof renderMathInElement !== 'function') {
      setTimeout(renderKatexWhenReady, 80);
      return;
    }
    renderMathInElement(document.body, {
      delimiters: [
        {left: '$$', right: '$$', display: true},
        {left: '\\[', right: '\\]', display: true},
        {left: '$', right: '$', display: false},
        {left: '\\(', right: '\\)', display: false}
      ],
      throwOnError: false
    });
  }
  renderKatexWhenReady();
});
</script>
"""
            content = content.replace("</head>", f"{katex}</head>")

        # Make href="#xxx" work inside iframe via internal scroll.
        anchor_fix = """
<script>
document.addEventListener('DOMContentLoaded', function() {
    // intercept a[href^="#"] and perform iframe-internal scroll
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
        if "</body>" in content:
            content = content.replace("</body>", f"{anchor_fix}</body>")
        else:
            content += anchor_fix

        return content

    return "<html><body><h1>找不到論文</h1></body></html>"
