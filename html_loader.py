#!/usr/bin/env python3
"""HTML loading utilities for Paper Story Rewriting Center."""

import re
from pathlib import Path


STORYTELLERS_DIR = Path.home() / "Documents" / "Storytellers"

# ── Markdown list repair ─────────────────────────────────────────────────────

_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAGS_RE = re.compile(r"<[^>]+>")
_BULLET_RE = re.compile(r"^[-*]\s+")
_ORDERED_RE = re.compile(r"^\d+\.\s+")
_P_RE = re.compile(r"<p>(.*?)</p>", re.DOTALL | re.IGNORECASE)
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s\-|:]+\|?\s*$")


def _fix_markdown_lists_in_html(content: str) -> str:
    """Convert <p> blocks containing raw markdown bullet lines to proper <ul>/<li> HTML.

    When the Python markdown library fails to detect list items (because no
    blank line precedes them), the output is a <p> where each line starts with
    ``* `` or ``- `` separated by ``<br />``.  This function detects that
    pattern and replaces the paragraph with a properly nested ``<ul>``.
    """

    def _process_p(m: re.Match) -> str:
        inner = m.group(1)
        lines = _BR_RE.split(inner)

        rows = []
        for raw in lines:
            # Strip tags but keep whitespace to measure indentation
            text_no_tags = _TAGS_RE.sub("", raw)
            text = text_no_tags.strip()
            if not text:
                continue
            indent = len(text_no_tags) - len(text_no_tags.lstrip())
            is_bullet = bool(_BULLET_RE.match(text))
            is_ordered = bool(_ORDERED_RE.match(text))
            is_list = is_bullet or is_ordered
            if is_bullet:
                clean_html = re.sub(r"^\s*[-*]\s+", "", raw.strip())
            elif is_ordered:
                clean_html = re.sub(r"^\s*\d+\.\s+", "", raw.strip())
            else:
                clean_html = raw.strip()
            rows.append({"indent": indent, "html": clean_html, "bullet": is_list, "ordered": is_ordered})

        if not any(r["bullet"] for r in rows):
            return m.group(0)  # nothing to convert

        # Normalize indent levels relative to the minimum bullet indent.
        # Detect the actual indent step size from the data (usually 2 or 4 spaces).
        indent_vals = sorted({r["indent"] for r in rows if r["bullet"]})
        min_indent = indent_vals[0]
        if len(indent_vals) >= 2:
            diffs = [indent_vals[i + 1] - indent_vals[i] for i in range(len(indent_vals) - 1)]
            step = max(1, min(diffs))
        else:
            step = 4  # default; no nesting present
        for r in rows:
            r["level"] = max(0, (r["indent"] - min_indent) // step) if r["bullet"] else -1

        out: list = []
        depth = 0
        depth_ordered: list = []  # track <ol> vs <ul> per nesting level

        for row in rows:
            if row["level"] < 0:
                # Non-list line: close any open lists, emit as <p>
                while depth > 0:
                    out.append("</ol>" if depth_ordered[-1] else "</ul>")
                    depth_ordered.pop()
                    depth -= 1
                out.append(f'<p>{row["html"]}</p>')
                continue

            lv = row["level"]
            tag = "ol" if row["ordered"] else "ul"
            if depth == 0:
                out.append(f"<{tag}>")
                depth_ordered.append(row["ordered"])
                depth = 1
            # Open deeper levels
            while depth - 1 < lv:
                out.append(f"<{tag}>")
                depth_ordered.append(row["ordered"])
                depth += 1
            # Close too-deep levels
            while depth - 1 > lv:
                out.append("</ol>" if depth_ordered[-1] else "</ul>")
                depth_ordered.pop()
                depth -= 1
            out.append(f'<li>{row["html"]}</li>')

        while depth > 0:
            out.append("</ol>" if depth_ordered[-1] else "</ul>")
            depth_ordered.pop()
            depth -= 1

        return "\n".join(out)

    # Only process the <body> portion to avoid touching <style>/<script> blocks
    body_start = content.lower().find("<body")
    if body_start == -1:
        return _P_RE.sub(_process_p, content)

    head = content[:body_start]
    body = content[body_start:]
    return head + _P_RE.sub(_process_p, body)


def _fix_markdown_tables_in_html(content: str) -> str:
    """Convert <p> blocks containing raw GFM table syntax to proper <table> HTML.

    When Python markdown fails to parse a table (because no blank line precedes
    it), the output is a <p> where each row is ``| col |`` separated by ``<br>``.
    This function detects the pattern (consecutive ``|`` lines with a separator
    row at index 1) and replaces the paragraph with ``<table>`` HTML.
    """

    def _parse_cells(row: str) -> list:
        s = _TAGS_RE.sub("", row).strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        return [c.strip() for c in s.split("|")]

    def _process_p(m: re.Match) -> str:
        inner = m.group(1)
        raw_lines = _BR_RE.split(inner)
        clean = [_TAGS_RE.sub("", l).strip() for l in raw_lines]

        if not any(_TABLE_ROW_RE.match(c) for c in clean):
            return m.group(0)  # no table syntax present

        out: list = []
        changed = False
        i = 0
        while i < len(clean):
            if _TABLE_ROW_RE.match(clean[i]):
                # Collect consecutive | lines
                j = i
                while j < len(clean) and _TABLE_ROW_RE.match(clean[j]):
                    j += 1
                run_clean = clean[i:j]
                run_raw = raw_lines[i:j]
                # GFM table: ≥3 rows, second row is separator
                if len(run_clean) >= 3 and _TABLE_SEP_RE.match(run_clean[1]):
                    headers = _parse_cells(run_raw[0])
                    thead = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
                    tbody = "".join(
                        "<tr>" + "".join(f"<td>{c}</td>" for c in _parse_cells(r)) + "</tr>"
                        for r in run_raw[2:]
                    )
                    out.append(f"<table><thead>{thead}</thead><tbody>{tbody}</tbody></table>")
                    changed = True
                else:
                    for l in run_raw:
                        if l.strip():
                            out.append(f"<p>{l.strip()}</p>")
                i = j
            else:
                if clean[i]:
                    out.append(f"<p>{raw_lines[i].strip()}</p>")
                i += 1

        if not changed:
            return m.group(0)
        return "\n".join(out)

    body_start = content.lower().find("<body")
    if body_start == -1:
        return _P_RE.sub(_process_p, content)
    head = content[:body_start]
    body = content[body_start:]
    return head + _P_RE.sub(_process_p, body)


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

        # Repair markdown-style bullet lines that were not converted to <ul>/<li>
        # (affects files generated before the _ensure_list_blank_lines fix).
        content = _fix_markdown_lists_in_html(content)

        # Repair markdown-style table syntax that was not converted to <table>
        # (affects files generated before the table blank-line fix).
        content = _fix_markdown_tables_in_html(content)

        # Ensure <ul>/<ol>/<li> render with proper indentation and bullets.
        # Some generated HTML files omit explicit list CSS, causing browsers to
        # use default styles which may be overridden by a CSS reset.
        list_css = """
<style>
body ul, body ol { padding-left: 1.6em; margin: 0.5em 0; }
body ul { list-style-type: disc; }
body ol { list-style-type: decimal; }
body ul ul, body ul ol { list-style-type: circle; }
body ol ol, body ol ul { list-style-type: lower-alpha; }
body li { margin: 0.25em 0; }
body table { border-collapse: collapse; margin: 0.75em 0; width: auto; }
body th, body td { border: 1px solid #ccc; padding: 4px 10px; text-align: left; }
body thead tr { background: #f0f0f0; font-weight: bold; }
body tbody tr:nth-child(even) { background: #fafafa; }
</style>
"""
        if "</head>" in content:
            content = content.replace("</head>", f"{list_css}</head>")

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
