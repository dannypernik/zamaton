#!/usr/bin/env python3
"""
Generate SAT content: question images, math/RW PDF worksheets, and practice test PDFs.

Usage
-----
  python scripts/generate.py images          [--out DIR] [--ids id1,id2] [--workers N]
  python scripts/generate.py math            [--out DIR] [--skill "Linear functions"] [--workers N]
  python scripts/generate.py rw              [--out DIR] [--skill "Transitions"] [--workers N]
  python scripts/generate.py practice-tests  [--out DIR] [--tests SAT4,PSAT1] [--workers N]

Requirements
------------
  pip install playwright requests pypdf
  playwright install chromium
"""

import argparse
import asyncio
import base64
import csv
import io
import json
import re
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path

import requests
from playwright.async_api import async_playwright, Browser
from pypdf import PdfWriter, PdfReader

# ══════════════════════════════════════════════════════════════════════════════
# PATHS & CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

_REPO     = Path(__file__).resolve().parent
_TP       = _REPO.parent / "test-prep-analysis/src/data"
_FONT_DIR = Path.home() / "Library/Fonts"

_FIGURES   = _REPO / "question-images/figures"
_MATH_BANK = _TP / "math-question-bank.json"
_RW_BANK   = _TP / "reading-question-bank.json"
_PT_JSON   = _TP / "practice-test-questions.json"

_MATH_EXPL = _REPO / "math-question-bank-explanations.json"
_RW_EXPL   = _REPO / "reading-question-bank-explanations.json"

MASTER_SS_ID = "1XoANqHEGfOCdO1QBVnbA3GH-z7-_FMYwoy7Ft4ojulE"
QB_DATA_GID  = "64410753"
QB_DATA_URL  = (
    f"https://docs.google.com/spreadsheets/d/{MASTER_SS_ID}"
    f"/export?format=csv&gid={QB_DATA_GID}"
)

# Practice test roster
_SAT_TESTS  = [f"SAT{n}" for n in range(4, 12)]   # SAT4 … SAT11
_PSAT_TESTS = ["PSAT1", "PSAT2"]
_ALL_TESTS  = _SAT_TESTS + _PSAT_TESTS
_MODULE_ORDER = [("RW", 1), ("RW", 2), ("RW", 3), ("M", 1), ("M", 2), ("M", 3)]

# Source/code prefixes that indicate a live (unreleased) test question → tombstone.
# SAT1-3 are published practice tests whose questions can appear in worksheets;
# SAT4+ and PSAT are live/unreleased tests.
_LIVE_SRC = re.compile(r"^(SAT[4-9]|SAT[1-9][0-9]|PSAT)", re.IGNORECASE)

# Base URL for question image fallbacks (used when an ID is not in the JSON bank)
_OPEN_PATH_IMG = "https://www.openpathtutoring.com/static/img/concepts/sat"

# Second save location for concept worksheets and answer keys
_CONCEPTS_ROOT = Path("/Users/danny/Projects/Open Path/_Tutoring/_Test Prep/SAT/_Concepts")
_CONCEPTS_MATH = _CONCEPTS_ROOT / "_Math"
_CONCEPTS_RW   = _CONCEPTS_ROOT / "_Reading & Writing"


def _fetch_image_fallback(q_id: str, subject: str) -> bytes | None:
    """Try to fetch a question image from openpathtutoring.com.

    *subject* should be ``"math"`` or ``"rw"``.
    Returns raw JPEG bytes on success, None if not found or on error.
    """
    url = f"{_OPEN_PATH_IMG}/{subject}/{q_id}.jpg"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception:
        pass
    return None


def _image_fallback_question(q_id: str, subject: str) -> dict | None:
    """Return a synthetic question dict built from the openpathtutoring image.

    The returned dict has an HTML <figure> as its question content so
    build_worksheet_html renders it as a full-width image card.
    Returns None if the image is not available.
    """
    img_bytes = _fetch_image_fallback(q_id, subject)
    if img_bytes is None:
        return None
    b64 = base64.b64encode(img_bytes).decode()
    return {
        "question": (
            f'<figure class="image question-fallback">'
            f'<img src="data:image/jpeg;base64,{b64}" alt="Question {q_id}">'
            f'</figure>'
        ),
        "answers": {},
        "type": "image",
        "_image_fallback": True,
    }

# ══════════════════════════════════════════════════════════════════════════════
# HTML PREPROCESSING
# ══════════════════════════════════════════════════════════════════════════════

def clean_html(html: str, math_mode: bool = True) -> str:
    """Strip parasitic blank paragraphs and fix MathML spacing operators."""
    # Remove <p ...>&nbsp;</p> / <p ...> </p>
    html = re.sub(
        r"<p(?:\s[^>]*)?>(?:\s|&nbsp;)*</p>",
        "",
        html,
        flags=re.IGNORECASE,
    )
    # Merge punctuation-only <p> into the preceding </p> (common CKEditor artifact)
    html = re.sub(
        r"</p>\s*<p(?:\s[^>]*)?>([.,:;?!])\s*</p>",
        r"\1</p>",
        html,
        flags=re.IGNORECASE,
    )
    # Replace <mo>&#160;</mo> (non-breaking space as spacer) with a thin mspace
    html = re.sub(
        r"<mo>\s*(?:&#160;|&nbsp;| )\s*</mo>",
        '<mspace width="0.3em"/>',
        html,
        flags=re.IGNORECASE,
    )
    # Add rspace to a minus <mo> before <mfrac> so the minus doesn't merge with the bar
    html = re.sub(
        r'<mo>(\s*-\s*)</mo>(\s*)(<mfrac)',
        r'<mo rspace="0.3em">\1</mo>\2\3',
        html,
        flags=re.IGNORECASE,
    )
    if math_mode:
        # Convert <span class="italic"> (and font_style:italic variants) to MathML <mi>.
        # All observed values are single- or multi-letter math variable/geometry identifiers.
        html = re.sub(
            r'<span\s+class="[^"]*(?:\bitalic\b|font_style:italic)[^"]*"[^>]*>(.*?)</span>',
            _italic_span_to_mi,
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
    return html


def _italic_span_to_mi(m: re.Match) -> str:
    """Convert an italic <span> to MathML <mi> element(s)."""
    inner = m.group(1)

    # Strip non-breaking spaces
    inner = inner.replace('&nbsp;', '').replace('\xa0', '').strip()

    # Peel off trailing punctuation to keep outside the <math> element
    suffix = ''
    while inner and inner[-1] in ',.;':
        suffix = inner[-1] + suffix
        inner = inner[:-1].rstrip()

    if not inner:
        return suffix

    # Handle subscript: letters<sub>sub</sub>
    sub_m = re.match(r'^([A-Za-z]+)<sub[^>]*>([A-Za-z0-9]+)</sub>$', inner.strip())
    if sub_m:
        base = sub_m.group(1)
        sub  = sub_m.group(2)
        mi_base = ''.join(f'<mi>{c}</mi>' for c in base)
        return f'<math><msub><mrow>{mi_base}</mrow><mi>{sub}</mi></msub></math>{suffix}'

    # Plain letters only: split into one <mi> per character so each renders math-italic
    plain = re.sub(r'<[^>]+>', '', inner)
    if re.fullmatch(r'[A-Za-z]+', plain):
        mi_str = ''.join(f'<mi>{c}</mi>' for c in plain)
        return f'<math>{mi_str}</math>{suffix}'

    # Fallback for anything unexpected: apply italic via CSS
    return f'<span style="font-style:italic">{inner}</span>{suffix}'


def fix_mfenced(html: str) -> str:
    """Chromium (MathML Core) dropped <mfenced>; replace with explicit <mo> brackets."""
    def replacer(m: re.Match) -> str:
        attrs, content = m.group(1), m.group(2)
        o_m = re.search(r"""open=['"]([^'"]*)['"]""", attrs)
        c_m = re.search(r"""close=['"]([^'"]*)['"]""", attrs)
        o = o_m.group(1) if o_m else "("
        c = c_m.group(1) if c_m else ")"
        return (
            f'<mrow><mo stretchy="false">{o}</mo>'
            f"<mrow>{content}</mrow>"
            f'<mo stretchy="false">{c}</mo></mrow>'
        )

    prev = None
    while prev != html:
        prev = html
        html = re.sub(r"<mfenced([^>]*)>(.*?)</mfenced>", replacer, html, flags=re.DOTALL)
    return html


def is_empty_html(html: str) -> bool:
    """True when HTML has no visible content (text or images)."""
    if re.search(r'<img\b', html, re.IGNORECASE):
        return False
    return not re.sub(r"<[^>]+>", "", html).strip()


def _wrap_final_punct(html: str) -> str:
    """Bind punctuation to the preceding item so it never starts a new line."""
    _P = r'[.,:;?]'
    # Case 1: <math>...</math> followed by punctuation (anywhere in text)
    html = re.sub(
        r'(<math(?:[^>]*)>(?:(?!</math>).)*</math>)\s*(' + _P + r')',
        r'<span style="white-space: nowrap">\1\2</span>',
        html, flags=re.DOTALL | re.IGNORECASE,
    )
    # Case 2: <img .../> followed by punctuation (anywhere — overline notation etc.)
    # Safe because the img regex consumes the full tag before looking for punct.
    html = re.sub(
        r'(<img\b[^>]*/?>)\s*(' + _P + r')',
        r'<span style="white-space: nowrap">\1\2</span>',
        html, flags=re.IGNORECASE,
    )
    # Case 3: plain-text word before punctuation at end of paragraph only.
    # Restricted to </p> boundary to avoid matching inside attribute values.
    html = re.sub(
        r'(?<=[\s>])([^\s<>]+)\s*(' + _P + r')(?=\s*</p>)',
        r'<span style="white-space: nowrap">\1\2</span>',
        html, flags=re.IGNORECASE,
    )
    return html


def _move_svg_legend_right(svg: str) -> str:
    """Move the bottom legend box to the right of the chart; shrink SVG height."""
    border_m = re.search(
        r'<rect\b([^>]*)fill=["\']none["\']([^>]*)(?:/>|></rect>)',
        svg, re.IGNORECASE,
    )
    if not border_m:
        return svg

    all_attrs = border_m.group(1) + border_m.group(2)

    def _fa(attrs: str, name: str) -> float | None:
        # (?<![a-zA-Z0-9_-]) prevents matching stroke-width when name="width"
        m = re.search(r'(?<![a-zA-Z0-9_-])' + name + r'="([\d.]+)"', attrs)
        return float(m.group(1)) if m else None

    legend_y = _fa(all_attrs, 'y')
    legend_h = _fa(all_attrs, 'height')
    legend_x = _fa(all_attrs, 'x')
    legend_w = _fa(all_attrs, 'width')
    if None in (legend_y, legend_h, legend_x, legend_w):
        return svg

    root_m = re.match(r'(<svg\b[^>]*>)', svg, re.IGNORECASE)
    if not root_m:
        return svg
    root_tag = root_m.group(1)
    root_plain = root_tag.replace('px"', '"')
    old_h = _fa(root_plain, 'height')
    old_w = _fa(root_plain, 'width')
    if not old_h or not old_w:
        return svg

    gap = 16  # horizontal gap between chart right edge and legend left edge

    tail_pos = svg.rfind('</g></svg>')
    if tail_pos < 0:
        return svg

    # Primary y-coordinate of a single SVG element string
    def _elem_y(el: str) -> float | None:
        # SVG allows both translate(tx,ty) and translate(tx ty)
        t = re.search(r'transform="translate\(\s*[\d.]+\s*[,\s]\s*([\d.]+)', el)
        if t:
            return float(t.group(1))
        y1 = re.search(r'\by1="([\d.]+)"', el)
        if y1:
            return float(y1.group(1))
        y = re.search(r'(?<![a-zA-Z0-9_-])y="([\d.]+)"', el)
        if y:
            return float(y.group(1))
        return None

    # Max visual y-extent of an element (bars use y+height; others use y)
    def _elem_max_y(el: str) -> float | None:
        base = _elem_y(el)
        if base is None:
            return None
        h_m = re.search(r'(?<![a-zA-Z0-9_-])height="([\d.]+)"', el)
        if h_m:
            return base + float(h_m.group(1))
        y2 = re.search(r'\by2="([\d.]+)"', el)
        if y2:
            return max(base, float(y2.group(1)))
        return base

    # Classify elements after the border rect: legend items (y inside legend box)
    # vs chart data drawn on top of the legend (y outside legend box).
    # SVGs sometimes draw chart series after the legend in document order for z-ordering.
    after_border = svg[border_m.start() + len(border_m.group(0)):tail_pos]
    legend_items = [border_m.group(0)]
    chart_over   = []
    y_lo = legend_y - 10
    y_hi = legend_y + legend_h + 10

    for el_m in re.finditer(
        r'<(?:line|rect|text|path|circle|polygon|polyline)\b[^>]*'
        r'(?:/>|>(?:.*?)</(?:line|rect|text|path|circle|polygon|polyline)>)?',
        after_border, re.DOTALL | re.IGNORECASE,
    ):
        el = el_m.group(0)
        y  = _elem_y(el)
        if y is not None and y_lo <= y <= y_hi:
            legend_items.append(el)
        else:
            chart_over.append(el)

    # Compute chart content bottom from pre-legend + chart_over elements,
    # then add a buffer for rotated axis labels that visually extend below their anchor.
    chart_content = svg[:border_m.start()] + "".join(chart_over)
    chart_max_y = 0.0
    for el_m in re.finditer(
        r'<(?:line|rect|text)\b[^>]*>', chart_content, re.IGNORECASE
    ):
        my = _elem_max_y(el_m.group(0))
        if my is not None and my < legend_y:
            chart_max_y = max(chart_max_y, my)
    # Detect diagonally-rotated text near the chart bottom (angled x-axis labels).
    # These visually extend well below their y anchor. y-axis labels (rotate -90)
    # are positioned mid-height and won't be near chart_max_y, so they're excluded.
    bottom_threshold = chart_max_y - 50
    has_angled_bottom_text = any(
        re.search(r'rotate\(', el_m.group(0), re.IGNORECASE)
        and _elem_y(el_m.group(0)) is not None
        and _elem_y(el_m.group(0)) >= bottom_threshold
        for el_m in re.finditer(r'<text\b[^>]*>', chart_content, re.IGNORECASE)
    )
    new_h = chart_max_y + (120 if has_angled_bottom_text else 30)

    right_pad = 10  # keep legend right border stroke inside viewBox
    new_w = old_w + gap + legend_w + right_pad

    dx = old_w + gap - legend_x
    dy = new_h / 2 - legend_h / 2 - legend_y

    legend_group = (
        f'<g transform="translate({dx:.1f} {dy:.1f})">'
        + "".join(legend_items)
        + "</g>"
    )
    result = (
        chart_content
        + legend_group
        + svg[tail_pos:]
    )

    def _set_attr(tag: str, name: str, val: float) -> str:
        return re.sub(
            r'(?<![a-zA-Z0-9_-])' + name + r'="[\d.]+(?:px)?"',
            f'{name}="{val:.2f}"',
            tag, count=1, flags=re.IGNORECASE,
        )

    new_root = _set_attr(root_tag, 'height', new_h)
    new_root = _set_attr(new_root, 'width',  new_w)

    vb_m = re.search(r'(?i)viewbox="([^"]+)"', new_root)
    if vb_m:
        parts = vb_m.group(1).split()
        if len(parts) == 4:
            parts[2] = f'{new_w:.2f}'
            parts[3] = f'{new_h:.2f}'
            new_root = (
                new_root[:vb_m.start()]
                + f'viewBox="{" ".join(parts)}"'
                + new_root[vb_m.end():]
            )

    return new_root + result[root_m.end():]


_svg_counter = 0

def _uniquify_svg_ids(svg: str) -> str:
    """Prefix every id/url(#…)/href=#… in an SVG with a unique token.

    Without this, multiple SVGs on the same page that all define id="marker0"
    cause every url(#marker0) to resolve to the *first* definition in the DOM,
    making markers/clip-paths/patterns wrong or invisible on later questions.
    """
    global _svg_counter
    _svg_counter += 1
    prefix = f"svg{_svg_counter}-"

    # Collect all ids defined in this SVG
    ids = set(re.findall(r'\bid="([^"]+)"', svg, re.IGNORECASE))
    if not ids:
        return svg

    for old_id in sorted(ids, key=len, reverse=True):  # longest first to avoid partial hits
        new_id = prefix + old_id
        # Replace id="…" definition
        svg = re.sub(r'(?i)\bid="' + re.escape(old_id) + r'"',
                     f'id="{new_id}"', svg)
        # Replace url(#…) references
        svg = re.sub(r'url\(#' + re.escape(old_id) + r'\)',
                     f'url(#{new_id})', svg)
        # Replace href="#…" references
        svg = re.sub(r'href="#' + re.escape(old_id) + r'"',
                     f'href="#{new_id}"', svg)

    return svg


def _fix_mover_overlines(html: str) -> str:
    """Convert MathML <mover> segment overlines to CSS text-decoration.

    Chromium's MathML renderer does not stretch <mo>&#175;</mo> (MACRON)
    horizontally over multi-letter bases, so segment notation like RS̄ renders
    with a tiny non-spanning bar.  All 51 occurrences in the question bank are
    two-letter pairs of <mi> elements; replace the entire
    <math><mover><mrow>XY</mrow><mo>&#175;</mo></mover></math> with a
    <span class="seg-overline">XY</span> that uses CSS text-decoration:overline.
    """
    def _repl(m: re.Match) -> str:
        mrow = m.group(1)
        # Skip anything with complex sub-structure (fractions, nested overs, etc.)
        if re.search(r'<(?:mfrac|msup|msub|msqrt|mover|munder)\b', mrow, re.IGNORECASE):
            return m.group(0)
        letters = re.sub(r'<[^>]+>', '', mrow).strip()
        if not letters:
            return m.group(0)
        return f'<span class="seg-overline">{letters}</span>'

    return re.sub(
        r'<math\b[^>]*>\s*<mover\b[^>]*>\s*<mrow>(.*?)</mrow>\s*'
        r'<mo[^>]*>&#175;</mo>\s*</mover>\s*</math>',
        _repl,
        html, flags=re.DOTALL | re.IGNORECASE,
    )


def prep_html(html: str, math_mode: bool = True) -> str:
    html = fix_mfenced(clean_html(html, math_mode=math_mode))
    html = _fix_mover_overlines(html)
    html = re.sub(
        r'<svg\b[^>]*>.*?</svg>',
        lambda m: _uniquify_svg_ids(_move_svg_legend_right(m.group(0))),
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Extract leading base64 figure images from block elements into a
    # standalone <figure class="image"> so they receive figure sizing.
    html = re.sub(
        r'(<(?:div|p)\b[^>]*>)\s*(<img\b(?=[^>]*src="data:image/)[^>]*/?>)',
        r'<figure class="image">\2</figure>\1',
        html, flags=re.IGNORECASE,
    )
    html = _wrap_final_punct(html)
    return html


# ══════════════════════════════════════════════════════════════════════════════
# WORKSHEET / PRACTICE-TEST: CSS + HTML
# ══════════════════════════════════════════════════════════════════════════════

def _worksheet_css(font_dir: Path) -> str:
    fd = str(font_dir).replace("\\", "/")
    return f"""
@font-face {{
  font-family: 'Montserrat';
  src: url('file://{fd}/Montserrat-Regular.ttf');
  font-weight: 400; font-style: normal;
}}
@font-face {{
  font-family: 'Montserrat';
  src: url('file://{fd}/Montserrat-Bold.ttf');
  font-weight: 700; font-style: normal;
}}
@font-face {{
  font-family: 'Montserrat';
  src: url('file://{fd}/Montserrat-SemiBold.ttf');
  font-weight: 600; font-style: normal;
}}

@page {{
  size: letter;
  margin: 0.45in 0.9in 0.65in 0.9in;
}}

*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
  font-family: 'Noto Serif', Times, serif;
  font-size: 10pt;
  line-height: 14pt;
  color: #1a1a1a;
  background: #fff;
}}

/* ── Page header ──────────────────────────────────── */
.page-header {{
  text-align: center;
  margin-bottom: 0;
}}
.page-title {{
  font-family: 'Montserrat', sans-serif;
  font-weight: 400;
  font-size: 14pt;
  line-height: 18pt;
}}
.page-subtitle {{
  font-family: 'Montserrat', sans-serif;
  font-weight: 400;
  font-size: 11pt;
  line-height: 14pt;
  margin-top: 4pt;
  margin-bottom: 6pt;
}}
.title-rule {{
  border: none;
  border-top: 0.5pt solid #000;
  margin: 8pt 0 14pt 0;
}}

/* ── Question block ───────────────────────────────── */
.question-block {{
  break-inside: avoid;
}}

.question-header {{
  display: flex;
  align-items: baseline;
  margin-bottom: 5pt;
  line-height: 14pt;
}}
.question-number {{
  font-family: 'Montserrat', sans-serif;
  font-weight: 700;
  font-size: 10pt;
  white-space: nowrap;
  min-width: 0.55in;
  padding-right: 10pt;
  flex-shrink: 0;
}}
.question-id {{
  font-family: 'Montserrat', sans-serif;
  font-weight: 400;
  font-size: 10pt;
  color: #777777;
}}

/* ── Content areas ────────────────────────────────── */
.prompt-text {{
  margin-bottom: 14pt;
}}
.question-text {{
  margin-bottom: 0;
}}
/* Math-expression images (overline notation, inline equations)
   rendered as PNGs with class="math-img". Display at natural size so
   overline bars fully span both letters. Vertical alignment is set
   per-image by _DOM_FIXUPS JS based on each image's naturalHeight. */
img.math-img {{
  height: auto !important;
  width: auto !important;
  max-width: none !important;
  display: inline-block !important;
}}
/* Segment overline notation (replaces MathML mover) */
.seg-overline {{
  text-decoration: overline;
  text-decoration-thickness: 1px;
  font-style: italic;
  white-space: nowrap;
}}
/* figure.image img overrides the inline rule above */
figure.image img {{
  height: 130pt !important;
  max-height: 180pt !important;
  width: auto !important;
  vertical-align: initial !important;
}}

/* Hide screen-reader-only content */
.sr-only,
[class="sr-only"],
div[role="region"][aria-label] {{
  display: none !important;
}}

/* Figures and inline SVG */
figure.image {{
  margin: 6pt 0 10pt 0;
  display: block;
  text-align: center;
}}
figure.image img {{
  height: 130pt;
  max-width: 70%;
  width: auto;
  display: inline-block;
}}
figure.image svg {{
  max-height: 180pt !important;
  max-width: 70% !important;
  width: auto !important;
  display: inline-block;
}}
/* Full-question image fallback (from openpathtutoring.com).
   Override the 130pt height cap so the complete question is visible. */
figure.question-fallback img {{
  height: auto !important;
  max-height: none !important;
  width: 100% !important;
  max-width: 100% !important;
  display: block !important;
}}

/* Tables */
figure.table {{
  margin: 6pt 0 10pt 0;
  display: block;
  width: 100%;
  overflow: hidden;
}}
table {{
  border-collapse: collapse;
  width: 100%;
  max-width: 100%;
  table-layout: auto;
}}
td, th {{
  padding: 4px 10px;
  border: 1px solid #999;
  text-align: center;
  vertical-align: middle;
  font-size: 9pt;
  word-break: break-word;
}}
th {{ background: #f0f0f0; font-weight: 600; }}

/* Paragraphs */
p {{ margin-bottom: 8pt; text-align: justify; }}
p[style*="text-align: center"] {{ text-align: center !important; }}
p[style*="text-align:center"] {{ text-align: center !important; }}
p[style*="text-align: right"]  {{ text-align: right  !important; }}
p[style*="text-align:right"]   {{ text-align: right  !important; }}
p[style*="text-align: left"]   {{ text-align: left   !important; }}
/* CollegeBoard/QTI class names that indicate a centred block equation */
.standalone_statement,
.para_center,
[class~="align:center"] {{ text-align: center !important; }}
/* math-img inside any centred context: block + auto margins */
.standalone_statement img.math-img,
.para_center img.math-img,
[class~="align:center"] img.math-img,
p[style*="text-align: center"] img.math-img,
p[style*="text-align:center"]  img.math-img {{
  display: block !important;
  margin-left: auto !important;
  margin-right: auto !important;
}}
p:last-child {{ margin-bottom: 0; }}

/* MathML — boost slightly to compensate for math font optical size vs body serif */
math {{ font-size: 1.15em !important; line-height: 1; }}
mi, mn, mtext, ms, mo, mspace {{ font-size: 1em !important; }}
.answer-text math {{ font-size: 1.2em !important; }}
msup > *:nth-child(2),
msub > *:nth-child(2),
msubsup > *:nth-child(3) {{ font-size: 0.82em; }}
/* Fraction bar breathing room */
mfrac > *:first-child {{ padding-bottom: 0.12em; }}
mfrac > *:last-child  {{ padding-top: 0.12em; }}

/* ── MCQ: extra space below question block for scratch work ── */
.question-block.question-mcq {{
  margin-bottom: 22pt;
}}

/* ── SPR: more space below + answer box ──────────────── */
.question-block.question-spr {{
  margin-bottom: 44pt;
}}
.spr-answer-box {{
  width: 77pt;
  height: 33pt;
  border: 1pt solid #515251;
  border-radius: 5pt;
  margin-top: 10pt;
  display: flex;
  align-items: flex-end;
  padding: 0 8pt 8pt 8pt;
}}
.spr-answer-line {{
  width: 100%;
  height: 0;
  border-bottom: 0.75pt solid #444;
}}

/* ── Answer choices ──────────────────────────────────── */
/* display:table + table-cell vertical-align:middle is    */
/* the most reliable vertical-centering in Chromium print.*/
.answers-list {{
  margin-top: 8pt;
  display: flex;
  flex-direction: column;
  width: max-content;
  min-width: 175pt;
  max-width: 98%;
  gap: 5pt;
}}
.answer-choice {{
  display: table;
  width: 100%;
  border: 1pt solid #515251;
  border-radius: 5pt;
  border-collapse: separate;
  overflow: hidden;
  box-sizing: border-box;
  min-height: 26pt;
}}
.answer-letter-cell {{
  display: table-cell;
  vertical-align: middle;
  width: 26pt;
  padding: 5pt 0 5pt 10pt;
  white-space: nowrap;
}}
.answer-letter {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16pt;
  height: 16pt;
  border-radius: 50%;
  border: 1.5pt solid #515251;
  font-family: sans-serif;
  font-weight: 700;
  font-size: 8pt;
  color: #515251;
  line-height: 1;
}}
.answer-text {{
  display: table-cell;
  vertical-align: middle;
  font-size: 10pt;
  line-height: normal;
  text-align: left !important;
  padding: 5pt 10pt 5pt 10pt;
}}
.answer-text .math-container img,
.answer-text .math-img {{
  display: inline-block !important;
  vertical-align: middle;
  max-width: none;
  margin-top: 8pt;
}}
.answer-text math {{ vertical-align: baseline; }}
.answer-text figure.image {{ margin: 0; line-height: 0; }}
.answer-text figure.image svg {{ width: 90pt !important; height: auto !important; display: block; }}
.answer-text img {{ max-width: 90pt; height: auto !important; display: inline-block; vertical-align: middle; margin: 2pt 0; }}
.answer-text p:empty,
.answer-text p > br:only-child {{
  display: none;
  margin: 0;
  padding: 0;
}}

/* ── Tombstone (removed/practice-test) placeholder ──────── */
.question-block.question-tombstone {{
  margin-bottom: 14pt;
}}
.tombstone-text {{
  font-style: italic;
  color: #888888;
  font-size: 9pt;
}}
"""


def build_worksheet_html(
    skill: str,
    difficulty: str,
    questions: list,  # list of (q_num, q_id, question_dict)
    font_dir: Path,
    *,
    page_title: str = "SAT Math",
    page_subtitle: str | None = None,
    label_prefix: str | None = None,
    math_mode: bool = True,
) -> str:
    """Return a full HTML document for one worksheet or practice-test module."""
    css = _worksheet_css(font_dir)
    diff_num = _DIFFICULTY_ORDINAL.get(difficulty, 1)
    if page_subtitle is None:
        page_subtitle = f"{_display_name(skill)} {diff_num}"
    prefix = label_prefix if label_prefix is not None else str(diff_num)

    blocks_html = ""
    for q_num, q_id, q in questions:
        label = f"{prefix}.{q_num}"
        if q.get("_tombstone"):
            blocks_html += f"""
      <div class="question-block question-tombstone">
        <div class="question-header">
          <span class="question-number">{label}</span>
          <span class="question-id">{q_id}</span>
        </div>
        <div class="tombstone-text">Removed. Appears on a practice test.</div>
      </div>"""
            continue

        q_html     = prep_html(q.get("question", ""), math_mode=math_mode)
        prompt_raw = q.get("prompt", "")
        prompt_html = prep_html(prompt_raw, math_mode=math_mode) if not is_empty_html(prompt_raw) else ""
        answers    = {k: prep_html(v, math_mode=math_mode) for k, v in q.get("answers", {}).items()}
        # Normalize type: RW questions have no "type" field; infer from answers
        q_type = q.get("type") or ("mcq" if answers else "spr")

        prompt_section = (
            f'<div class="prompt-text">{prompt_html}</div>' if prompt_html else ""
        )

        fig_path = _FIGURES / f"{q_id}.png"
        if fig_path.exists():
            fig_b64 = base64.b64encode(fig_path.read_bytes()).decode()
            figure_section = (
                f'<figure class="image">'
                f'<img src="data:image/png;base64,{fig_b64}" alt="Figure">'
                f'</figure>'
            )
        else:
            figure_section = ""

        if q_type == "image":
            answers_section = ""
            block_class = "question-image"
        elif q_type == "mcq" and answers:
            choices_html = ""
            for letter in ("A", "B", "C", "D"):
                if letter in answers:
                    choices_html += f"""
          <div class="answer-choice">
            <div class="answer-letter-cell"><span class="answer-letter">{letter}</span></div>
            <div class="answer-text">{answers[letter]}</div>
          </div>"""
            answers_section = f'<div class="answers-list">{choices_html}\n        </div>'
            block_class = "question-mcq"
        else:
            answers_section = '<div class="spr-answer-box"><div class="spr-answer-line"></div></div>'
            block_class = "question-spr"

        blocks_html += f"""
      <div class="question-block {block_class}">
        <div class="question-header">
          <span class="question-number">{label}</span>
          <span class="question-id">{q_id}</span>
        </div>
        {prompt_section}
        {figure_section}
        <div class="question-text">{q_html}</div>
        {answers_section}
      </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <style>{css}</style>
</head>
<body>
  <div class="page-header">
    <div class="page-title">{page_title}</div>
    <div class="page-subtitle">{page_subtitle}</div>
  </div>
  <hr class="title-rule">
  {blocks_html}
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# PLAYWRIGHT RENDERING
# ══════════════════════════════════════════════════════════════════════════════

_DOM_FIXUPS = """() => {
    // Remove narrow pixel widths from tables (e.g. style="width:100px") so columns
    // aren't squished. Percentage widths (e.g. "25%") are intentional — leave them.
    document.querySelectorAll('table').forEach(t => {
        const w = t.style.width;
        if (w && /^\d+(\.\d+)?px$/.test(w.trim()) && parseFloat(w) < 400) {
            t.style.removeProperty('width');
        }
    });
    document.querySelectorAll('.answer-text figure.image svg').forEach(svg => {
        svg.removeAttribute('width');
        svg.removeAttribute('height');
    });
    document.querySelectorAll('.answer-text img').forEach(img => {
        img.removeAttribute('width');
        img.removeAttribute('height');
    });
    // Restore math-img images to natural size and align them consistently.
    // Different images have different naturalHeights (22–32 px), so a single
    // vertical-align value in CSS can't centre them all on the same optical
    // baseline.  Set it per-image: target = image centre at ~4px above baseline.
    document.querySelectorAll('img.math-img').forEach(img => {
        img.removeAttribute('width');
        img.removeAttribute('height');
        img.style.setProperty('height', 'auto', 'important');
        img.style.setProperty('width', 'auto', 'important');
        img.style.setProperty('max-width', 'none', 'important');
        // Walk up to the nearest block ancestor to detect text-align:center.
        let block = img.parentElement;
        while (block && getComputedStyle(block).display === 'inline') block = block.parentElement;
        const parentAlign = block && getComputedStyle(block).textAlign;
        const centered = parentAlign === 'center' || parentAlign === '-webkit-center';
        if (centered) {
            img.style.setProperty('display', 'block', 'important');
            img.style.setProperty('margin-left', 'auto', 'important');
            img.style.setProperty('margin-right', 'auto', 'important');
        } else {
            img.style.setProperty('display', 'inline-block', 'important');
            const va = -(img.naturalHeight / 2 - 2);
            img.style.setProperty('vertical-align', va + 'px', 'important');
        }
    });
    document.querySelectorAll('.answer-text p').forEach(p => {
        if (p.children.length === 0) {
            const t = p.textContent.replace(/ /g, '').trim();
            if (t === '') {
                p.style.display = 'none';
                p.style.margin = '0';
                p.style.padding = '0';
            }
        }
    });
    document.querySelectorAll('msqrt').forEach(el => {
        el.style.setProperty('position', 'relative');
        el.style.setProperty('top', '-0.18em');
        [...el.children].forEach(child => {
            child.style.setProperty('transform', 'translateY(0.18em)');
        });
    });
    // Expand a chart svg's viewBox so rotated content (e.g. long angled axis
    // labels) is never clipped. _move_svg_legend_right's height estimate is a
    // flat buffer and can undershoot for long labels; getBBox() gives the
    // true rendered extent, so grow the viewBox/width/height to fit it.
    document.querySelectorAll('figure.image svg[viewBox]').forEach(svg => {
        let bbox;
        try { bbox = svg.getBBox(); } catch (e) { return; }
        if (!bbox || (!bbox.width && !bbox.height)) return;
        const vb = svg.viewBox.baseVal;
        const pad = 4;
        const minX = Math.min(vb.x, bbox.x - pad);
        const minY = Math.min(vb.y, bbox.y - pad);
        const maxX = Math.max(vb.x + vb.width, bbox.x + bbox.width + pad);
        const maxY = Math.max(vb.y + vb.height, bbox.y + bbox.height + pad);
        const newW = maxX - minX;
        const newH = maxY - minY;
        if (minX !== vb.x || minY !== vb.y || newW !== vb.width || newH !== vb.height) {
            const attrW = parseFloat(svg.getAttribute('width'));
            const attrH = parseFloat(svg.getAttribute('height'));
            svg.setAttribute('viewBox', `${minX} ${minY} ${newW} ${newH}`);
            if (attrW) svg.setAttribute('width', (attrW * newW / vb.width).toFixed(2));
            if (attrH) svg.setAttribute('height', (attrH * newH / vb.height).toFixed(2));
        }
    });
    // Math-italic 'h' is the Planck constant glyph (U+210E) — optically smaller;
    // boost the containing <math> when 'h' is the entire expression.
    document.querySelectorAll('math').forEach(math => {
        const txt = math.textContent.replace(/[\\s\\u00a0]/g, '');
        if (txt === 'h') {
            math.style.setProperty('font-size', '1.35em', 'important');
        }
    });
}"""


async def render_one(page, q_id: str, question: dict, out_dir: Path) -> None:
    html = build_worksheet_html(
        skill="",
        difficulty="",
        questions=[(1, q_id, question)],
        font_dir=_FONT_DIR,
        page_title="",
        page_subtitle="",
        label_prefix="",
    )
    await page.set_content(html, wait_until="networkidle")
    await page.evaluate(_DOM_FIXUPS)
    await page.add_style_tag(content="""
        .question-header { display: none !important; }
        .question-block  { padding: 3px 20px 12px !important; font-size: 13pt !important; line-height: 18pt !important; }
        .answer-text     { font-size: 13pt !important; line-height: 18pt !important; }
        .question-text,
        .prompt-text     { max-width: 60%; }
        .answers-list    { max-width: 60%; margin-top: 18pt !important; }
    """)

    block = await page.query_selector(".question-block")
    await block.screenshot(
        path=str(out_dir / f"{q_id}.jpg"),
        type="jpeg",
        quality=95,
        scale="device",
    )

async def render_worksheet(
    page,
    skill: str,
    difficulty: str,
    questions: list,
    out_dir: Path,
    font_dir: Path,
    **html_kwargs,
) -> None:
    pdf_filename = html_kwargs.pop("pdf_filename", None)
    html = build_worksheet_html(skill, difficulty, questions, font_dir, **html_kwargs)
    await page.set_content(html, wait_until="networkidle")
    await page.evaluate(_DOM_FIXUPS)

    diff_num = _DIFFICULTY_ORDINAL.get(difficulty, 1)
    filename = pdf_filename or f"{_display_name(skill)} {diff_num}.pdf"
    await page.pdf(
        path=str(out_dir / filename),
        format="Letter",
        print_background=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ANSWER KEY
# ══════════════════════════════════════════════════════════════════════════════

def build_answer_key_html(
    page_title: str,
    page_subtitle: str,
    questions: list,          # [(label, qid, q_dict), ...]
    explanations: dict,       # {qid: {correct, explanation, answers, ...}}
    font_dir: Path,
    sheet_answers: dict = (), # {qid: correct_answer} fallback from spreadsheet
    math_mode: bool = True,
) -> tuple[str, list[str]]:
    """Build a standalone HTML document for the answer key of one worksheet.

    Returns ``(html, missing_correct_ids)`` where *missing_correct_ids* is a
    list of ``"label (qid)"`` strings for questions whose correct answer is
    absent from the explanations JSON.
    """

    # Reuse the full worksheet CSS so questions render identically.
    base_css = _worksheet_css(font_dir)

    ak_css = """
/* ── Answer-key label row (between .page-header rule and title-rule) ── */
.ak-label {
  font-family: 'Montserrat', sans-serif;
  font-size: 9pt;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #888;
  margin-top: 2pt;
}

/* ── Per-entry spacing ─────────────────────────────────────────────── */
.ak-entry {
  margin-bottom: 20pt;
}

/* No SPR box in answer key — hide it */
.ak-entry .spr-answer-box { display: none !important; }
/* No extra SPR bottom margin in answer key */
.ak-entry .question-block.question-spr { margin-bottom: 8pt; }
/* Tighten MCQ bottom margin too */
.ak-entry .question-block.question-mcq { margin-bottom: 8pt; }

/* ── Correct-answer row ────────────────────────────────────────────── */
/* Glued to explanation so correct answer never sits alone at page bottom */
.ak-answer-row {
  break-after: avoid;
  display: flex;
  align-items: baseline;
  gap: 8pt;
  padding-left: 0.55in;
  margin-top: 8pt;
  margin-bottom: 5pt;
}
.ak-correct {
  font-family: 'Montserrat', sans-serif;
  font-weight: 700;
  font-size: 10pt;
  background: #1a1a1a;
  color: #fff;
  border-radius: 3pt;
  padding: 1pt 7pt 2pt 7pt;
  white-space: nowrap;
  flex-shrink: 0;
}
.ak-answer-text {
  font-size: 10pt;
  color: #333;
  font-style: italic;
}

/* ── Explanation body ──────────────────────────────────────────────── */
/* orphans:3 — at least 3 lines must stay at page bottom when paragraph
   fragments.  Combined with break-after:avoid on .ak-answer-row, the
   browser pushes the whole answer+explanation to the next page if fewer
   than 3 explanation lines would fit. */
.ak-body {
  padding-left: 0.55in;
}
.ak-body p {
  margin-bottom: 6pt;
  orphans: 3;
  widows: 2;
  text-align: left;
}
.ak-body p:last-child { margin-bottom: 0; }

/* Tombstone entries: answer-only, no question body */
.ak-tombstone {
  display: flex;
  align-items: baseline;
  gap: 8pt;
  padding-left: 0.55in;
}
"""

    missing_correct: list[str] = []
    entries_html = ""

    for label, qid, q in questions:
        if q.get("_image_fallback"):
            continue  # no answer key entry for image fallbacks

        is_tombstone = q.get("_tombstone", False)

        if is_tombstone:
            entries_html += f"""
<div class="ak-entry">
  <div class="question-block question-tombstone">
    <div class="question-header">
      <span class="question-number">{label}</span>
      <span class="question-id">{qid}</span>
    </div>
    <div class="tombstone-text">Removed. Appears on a practice test.</div>
  </div>
</div>"""
            continue

        # ── Regular question ──────────────────────────────────────────
        expl_data  = explanations.get(qid, {})
        correct    = expl_data.get("correct", "") or sheet_answers.get(qid, "")
        expl_html  = expl_data.get("explanation", "")
        answers    = q.get("answers") or expl_data.get("answers") or {}
        q_type     = q.get("type") or ("mcq" if answers else "spr")

        if not correct:
            missing_correct.append(f"{page_subtitle} — {label} ({qid})")

        if not correct and not expl_html:
            continue  # nothing useful to show

        # ── Rebuild full question HTML (same as worksheet) ────────────
        q_html      = prep_html(q.get("question", ""), math_mode=math_mode)
        prompt_raw  = q.get("prompt", "")
        prompt_html = prep_html(prompt_raw, math_mode=math_mode) if not is_empty_html(prompt_raw) else ""
        answers_prep = {k: prep_html(v, math_mode=math_mode) for k, v in answers.items()}

        prompt_section = (
            f'<div class="prompt-text">{prompt_html}</div>' if prompt_html else ""
        )

        fig_path = _FIGURES / f"{qid}.png"
        figure_section = ""
        if fig_path.exists():
            fig_b64 = base64.b64encode(fig_path.read_bytes()).decode()
            figure_section = (
                f'<figure class="image">'
                f'<img src="data:image/png;base64,{fig_b64}" alt="Figure">'
                f'</figure>'
            )

        if q_type == "image":
            answers_section = ""
            block_class = "question-image"
        elif q_type == "mcq" and answers_prep:
            choices_html = ""
            for letter in ("A", "B", "C", "D"):
                if letter in answers_prep:
                    choices_html += f"""
          <div class="answer-choice">
            <div class="answer-letter-cell"><span class="answer-letter">{letter}</span></div>
            <div class="answer-text">{answers_prep[letter]}</div>
          </div>"""
            answers_section = f'<div class="answers-list">{choices_html}\n        </div>'
            block_class = "question-mcq"
        else:
            answers_section = '<div class="spr-answer-box"><div class="spr-answer-line"></div></div>'
            block_class = "question-spr"

        # ── Correct-answer row ────────────────────────────────────────
        if q_type == "mcq" and correct in answers_prep:
            answer_text_html = answers_prep[correct]
            correct_row = (
                f'<div class="ak-answer-row">'
                f'<span class="ak-correct">{correct}</span>'
                f'<span class="ak-answer-text">{answer_text_html}</span>'
                f'</div>'
            )
        elif correct:
            correct_row = (
                f'<div class="ak-answer-row">'
                f'<span class="ak-correct">{correct}</span>'
                f'</div>'
            )
        else:
            correct_row = ""

        body_html = (
            f'<div class="ak-body">{prep_html(expl_html)}</div>'
            if expl_html else ""
        )

        entries_html += f"""
<div class="ak-entry">
  <div class="question-block {block_class}">
    <div class="question-header">
      <span class="question-number">{label}</span>
      <span class="question-id">{qid}</span>
    </div>
    {prompt_section}
    {figure_section}
    <div class="question-text">{q_html}</div>
    {answers_section}
  </div>
  {correct_row}
  {body_html}
</div>"""

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>{base_css}
{ak_css}</style>
</head>
<body>
<div class="page-header">
  <div class="page-title">{page_title}</div>
  <div class="page-subtitle">{page_subtitle}</div>
  <div class="ak-label">Answer Key</div>
</div>
<hr class="title-rule">
{entries_html}
</body>
</html>"""

    return html, missing_correct


async def render_answer_key(
    page,
    page_title: str,
    page_subtitle: str,
    questions: list,
    explanations: dict,
    out_dir: Path,
    font_dir: Path,
    pdf_filename: str,
    sheet_answers: dict = (),
    math_mode: bool = True,
) -> list:
    """Render an answer key PDF for one worksheet. Returns missing-correct list."""
    html, missing = build_answer_key_html(
        page_title, page_subtitle, questions, explanations, font_dir, sheet_answers,
        math_mode=math_mode,
    )
    await page.set_content(html, wait_until="networkidle")
    await page.evaluate(_DOM_FIXUPS)
    await page.pdf(
        path=str(out_dir / pdf_filename),
        format="Letter",
        print_background=True,
    )
    return missing


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════════════

def load_master_csv() -> list[dict]:
    """Fetch master spreadsheet and return all rows as a list of dicts."""
    print("Fetching Question bank data from master spreadsheet…")
    resp = requests.get(QB_DATA_URL, timeout=30)
    resp.raise_for_status()
    return list(csv.DictReader(io.StringIO(resp.text)))


def load_math_bank() -> dict:
    p = _MATH_BANK if _MATH_BANK.exists() else _REPO / _MATH_BANK.name
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_rw_bank() -> dict:
    p = _RW_BANK if _RW_BANK.exists() else _REPO / _RW_BANK.name
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_banks() -> dict:
    """Load both math and RW banks into a single {qid: question} dict."""
    banks = {}
    banks.update(load_math_bank())
    banks.update(load_rw_bank())
    return banks


def load_explanations(path: Path) -> dict:
    """Load an explanations JSON (math or RW).  Returns {} if file is missing."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════════════════════════
# MATH SKILL / DIFFICULTY HELPERS
# ══════════════════════════════════════════════════════════════════════════════

_DIFFICULTY_ORDINAL: dict[str, int] = {"Easy": 1, "Medium": 2, "Hard": 3}
_ORDINAL_TO_DIFF: dict[int, str]   = {1: "Easy", 2: "Medium", 3: "Hard"}

_SKILL_DISPLAY_NAMES: dict[str, str] = {
    "Equivalent expressions":                                                    "Equivalent Expressions",
    "Nonlinear equations in one variable and systems of equations in two variables": "Nonlinear Equations and Systems",
    "Nonlinear functions":                                                       "Nonlinear Functions",
    "Linear equations in one variable":                                          "Linear Equations in One Variable",
    "Linear equations in two variables":                                         "Linear Equations in Two Variables",
    "Linear functions":                                                          "Linear Functions",
    "Linear inequalities in one or two variables":                               "Linear Inequalities",
    "Systems of two linear equations in two variables":                          "Systems of Linear Equations",
    "Area and volume":                                                           "Area and Volume",
    "Circles":                                                                   "Circles",
    "Lines, angles, and triangles":                                              "Lines, Angles, and Triangles",
    "Right triangles and trigonometry":                                          "Right Triangles and Trigonometry",
    "One-variable data: Distributions and measures of center and spread":        "Distributions",
    "Two-variable data: Models and scatterplots":                                "Models and Scatterplots",
    "Percentages":                                                               "Percentages",
    "Probability and conditional probability":                                   "Probability",
    "Ratios, rates, proportional relationships, and units":                      "Ratios, Rates, Proportions, and Units",
    "Inference from sample statistics and margin of error":                      "Sample Statistics and Margin of Error",
    "Evaluating statistical claims: Observational studies and experiments":      "Statistical Claims",
}


def _display_name(skill: str) -> str:
    return _SKILL_DISPLAY_NAMES.get(skill.strip(), skill.strip())


# ══════════════════════════════════════════════════════════════════════════════
# RW SKILL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

_SKILL_NORM: dict[str, str] = {
    "Text Structure and Purpose": "Text, Structure, and Purpose",
    "Cross-text Connections":     "Cross-Text Connections",
}
_RW_DIFF_MAP: dict[str, str] = {"Easy": "1", "Medium": "2", "Hard": "3"}

WORKSHEET_SKILLS: set[str] = {
    "Boundaries",
    "Central Ideas and Details",
    "Command of Evidence",
    "Cross-Text Connections",
    "Form, Structure, and Sense",
    "Inferences",
    "Rhetorical Synthesis",
    "Text, Structure, and Purpose",
    "Transitions",
    "Words in Context",
}


def _norm_rw_skill(s: str) -> str:
    return _SKILL_NORM.get(s, s)


# ══════════════════════════════════════════════════════════════════════════════
# ASYNC WORKER POOL
# ══════════════════════════════════════════════════════════════════════════════

async def _worksheet_worker(browser: Browser, items: list, process_fn, progress: list) -> None:
    """Process items sequentially on a single browser page; call process_fn for each."""
    context = await browser.new_context(viewport={"width": 1200, "height": 900})
    page = await context.new_page()
    try:
        for item in items:
            try:
                label = await process_fn(page, item)
                progress.append(label)
                print(f"  [{len(progress)}] {label}")
            except Exception as exc:
                print(f"  ERROR: {exc}")
    finally:
        await context.close()


async def image_worker(browser: Browser, items: list, out_dir: Path, progress: list) -> None:
    context = await browser.new_context(
        viewport={"width": 1200, "height": 900},
        device_scale_factor=2,
    )
    page = await context.new_page()
    try:
        for q_id, question in items:
            try:
                await render_one(page, q_id, question, out_dir)
                progress.append(q_id)
                done = len(progress)
                if done % 50 == 0 or done == 1:
                    print(f"  {done} done...")
            except Exception as exc:
                print(f"  ERROR {q_id}: {exc}")
    finally:
        await context.close()


def _chunk(items: list, n: int) -> list[list]:
    size = max(1, (len(items) + n - 1) // n)
    return [items[i : i + size] for i in range(0, len(items), size)]


# ══════════════════════════════════════════════════════════════════════════════
# RUN: IMAGES
# ══════════════════════════════════════════════════════════════════════════════

async def run_images(args, math_questions: dict, rw_questions: dict) -> None:
    base_dir = Path(args.out)
    math_dir = base_dir / "math"
    rw_dir   = base_dir / "rw"
    math_dir.mkdir(parents=True, exist_ok=True)
    rw_dir.mkdir(parents=True, exist_ok=True)

    if args.ids:
        id_set = {i.strip() for i in args.ids.split(",")}
        math_questions = {k: v for k, v in math_questions.items() if k in id_set}
        rw_questions   = {k: v for k, v in rw_questions.items()   if k in id_set}

    math_items = list(math_questions.items())
    rw_items   = list(rw_questions.items())
    total      = len(math_items) + len(rw_items)
    print(f"Processing {len(math_items)} math + {len(rw_items)} RW images "
          f"with {args.workers} worker(s)...")

    progress: list = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        await asyncio.gather(
            *[image_worker(browser, chunk, math_dir, progress)
              for chunk in _chunk(math_items, args.workers)],
            *[image_worker(browser, chunk, rw_dir,   progress)
              for chunk in _chunk(rw_items,   args.workers)],
        )
        await browser.close()

    print(f"\nDone! {len(progress)}/{total} images saved to: {base_dir}/ (math/ and rw/)")

    if getattr(args, "regen_worksheets", False):
        regen_ids = set(math_questions) | set(rw_questions)
        ws_args = argparse.Namespace(skill=None, workers=args.workers, keys_only=False)

        print(f"\nRegenerating math worksheets affected by {len(regen_ids)} updated ID(s)...")
        ws_args.out = str(_REPO / "math-worksheets")
        await run_math_worksheets(ws_args, load_math_bank(), regen_ids)

        print(f"\nRegenerating RW worksheets affected by {len(regen_ids)} updated ID(s)...")
        ws_args.out = str(_REPO / "rw-worksheets")
        await run_rw_worksheets(ws_args, load_rw_bank(), regen_ids)


# ══════════════════════════════════════════════════════════════════════════════
# RUN: MATH WORKSHEETS
# ══════════════════════════════════════════════════════════════════════════════

async def run_math_worksheets(args, all_questions: dict, regen_ids: set | None = None) -> None:
    master_rows = load_master_csv()

    _ANY_TEST_CODE  = re.compile(r"^(SAT|SLT|PSAT)", re.IGNORECASE)
    _LIVE_TEST_CODE = re.compile(r"^(SAT[4-9]|SAT[1-9][0-9]|PSAT|SLT)", re.IGNORECASE)

    slot_map:       dict = defaultdict(dict)
    live_test_ids:  set  = set()
    all_master_ids: set  = set()

    sheet_answers: dict[str, str] = {}   # qid → correct answer from spreadsheet
    for row in master_rows:
        if row.get("Subject", "").strip() != "Math":
            continue
        qid  = row["ID"].strip()
        code = row.get("Skill code", "").strip()
        ans  = row.get("Answer", "").strip()
        all_master_ids.add(qid)
        if ans:
            sheet_answers[qid] = ans

        if _LIVE_TEST_CODE.match(code):
            live_test_ids.add(qid)
        if _ANY_TEST_CODE.match(code):
            continue

        m = re.match(r"^(.+?)\s+(\d+)\.(\d+)$", code)
        if not m:
            continue
        skill_d = m.group(1).strip()
        diff_n  = m.group(2)
        q_num   = int(m.group(3))
        if q_num not in slot_map[(skill_d, diff_n)]:
            slot_map[(skill_d, diff_n)][q_num] = qid

    tombstone_ids = {
        qid
        for slots in slot_map.values()
        for qid in slots.values()
        if qid in live_test_ids
    }
    worksheet_ids = {
        qid for slots in slot_map.values() for qid in slots.values()
        if qid not in tombstone_ids
    }
    print(f"  {len(worksheet_ids)} worksheet questions  |  {len(tombstone_ids)} tombstone(s)")
    if tombstone_ids:
        for (skill_d, diff_n), slots in sorted(slot_map.items()):
            ts = [(n, qid) for n, qid in sorted(slots.items()) if qid in tombstone_ids]
            if ts:
                print(f"  Tombstones in {skill_d} {diff_n}: "
                      + ", ".join(f"{n}={qid}" for n, qid in ts))

    # Validate consecutive q_nums
    for (skill_d, diff_n), slots in slot_map.items():
        q_nums = sorted(slots.keys())
        if q_nums != list(range(1, len(q_nums) + 1)):
            raise ValueError(
                f"Gap in '{skill_d}' diff={diff_n}: {q_nums!r} — fix the master spreadsheet."
            )

    # Append bank questions absent from master
    for qid, q in all_questions.items():
        if qid in all_master_ids:
            continue
        skill_d = _display_name(q.get("skill", ""))
        diff_n  = str(_DIFFICULTY_ORDINAL.get(q.get("difficulty", ""), 1))
        key = (skill_d, diff_n)
        next_num = max(slot_map[key].keys(), default=0) + 1
        slot_map[key][next_num] = qid

    fallback_used: list[str] = []
    missing_ids:   list[str] = []
    groups = []
    for (skill_d, diff_n), slots in sorted(slot_map.items()):
        difficulty = _ORDINAL_TO_DIFF.get(int(diff_n), "Easy")
        qs = []
        for q_num in sorted(slots.keys()):
            qid = slots[q_num]
            if qid in tombstone_ids:
                qs.append((q_num, qid, {"_tombstone": True, "correct": sheet_answers.get(qid, "")}))
            else:
                q = all_questions.get(qid)
                if q is None:
                    q = _image_fallback_question(qid, "math")
                    if q:
                        fallback_used.append(f"{skill_d} {diff_n}.{q_num} ({qid})")
                    else:
                        missing_ids.append(f"{skill_d} {diff_n}.{q_num} ({qid})")
                        continue
                qs.append((q_num, qid, q))
        if qs:
            groups.append((skill_d, difficulty, qs))

    if fallback_used:
        print(f"  ⚠  Image fallback used for {len(fallback_used)} question(s):")
        for s in fallback_used:
            print(f"       {s}")
    if missing_ids:
        print(f"  ✗  No data or image found for {len(missing_ids)} question(s) — skipped:")
        for s in missing_ids:
            print(f"       {s}")

    if args.skill:
        target = args.skill.strip().lower()
        groups = [g for g in groups if g[0].lower() == target]

    if regen_ids is not None:
        groups = [g for g in groups if any(qid in regen_ids for _, qid, _ in g[2])]

    total = len(groups)
    print(f"Generating {total} math worksheets with {args.workers} worker(s)...")

    out_dir      = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ak_dir       = out_dir / "_Answers"
    ak_dir.mkdir(parents=True, exist_ok=True)
    font_dir     = _FONT_DIR
    explanations = load_explanations(_MATH_EXPL)
    progress:    list = []
    missing_all: list = []

    async def process_fn(page, item):
        skill_d, difficulty, qs = item
        diff_num  = _DIFFICULTY_ORDINAL.get(difficulty, 1)
        pdf_name  = f"-{skill_d} {diff_num}.pdf"
        ak_name   = f"-{skill_d} {diff_num}~Key.pdf"
        subtitle  = f"{skill_d} {diff_num}"
        labelled  = [(f"{diff_num}.{q_num}", qid, q) for q_num, qid, q in qs]
        if not args.keys_only:
            await render_worksheet(page, skill_d, difficulty, qs, out_dir, font_dir,
                                   pdf_filename=pdf_name)
            concept_dir = _CONCEPTS_MATH / skill_d
            concept_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out_dir / pdf_name, concept_dir / pdf_name)
        if explanations:
            missing = await render_answer_key(
                page, "SAT Math", subtitle, labelled, explanations,
                ak_dir, font_dir, ak_name,
                sheet_answers=sheet_answers,
            )
            missing_all.extend(missing)
            ak_concept_dir = _CONCEPTS_MATH / "_Answers"
            ak_concept_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ak_dir / ak_name, ak_concept_dir / ak_name)
        return f"{skill_d} · {difficulty} ({len(qs)} questions)"

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        await asyncio.gather(*[
            _worksheet_worker(browser, chunk, process_fn, progress)
            for chunk in _chunk(groups, args.workers)
        ])
        await browser.close()

    print(f"\nDone! {len(progress)}/{total} worksheets saved to: {out_dir}/")
    if missing_all:
        print(f"  ⚠  Missing correct answer in JSON for {len(missing_all)} question(s):")
        for s in sorted(missing_all):
            print(f"       {s}")


# ══════════════════════════════════════════════════════════════════════════════
# RUN: RW WORKSHEETS
# ══════════════════════════════════════════════════════════════════════════════

async def run_rw_worksheets(args, rw_questions: dict, regen_ids: set | None = None) -> None:
    master_rows = load_master_csv()

    slot_map   = defaultdict(dict)  # (skill, diff_n) → {q_num: qid}
    master_ids = set()

    live_src_ids: set[str] = set()
    sheet_answers: dict[str, str] = {}   # qid → correct answer from spreadsheet

    for row in master_rows:
        if row.get("Subject", "").strip() != "Reading & Writing":
            continue
        qid  = row["ID"].strip()
        code = row.get("Skill code", "").strip()
        src  = row.get("Source", "").strip()
        ans  = row.get("Answer", "").strip()
        master_ids.add(qid)
        if ans:
            sheet_answers[qid] = ans

        # SAT4+ and PSAT are live/unreleased → tombstone.
        # SAT1-3 are published practice tests → include normally.
        if src and _LIVE_SRC.match(src):
            live_src_ids.add(qid)

        m = re.match(r"^(.+?)\s+(\d+)\.(\d+)$", code)
        if not m:
            continue
        skill_c = _norm_rw_skill(m.group(1).strip())
        if skill_c not in WORKSHEET_SKILLS:
            continue
        diff_n  = m.group(2)
        q_num   = int(m.group(3))
        slot_map[(skill_c, diff_n)][q_num] = qid

    tombstone_ids = {
        qid
        for slots in slot_map.values()
        for qid in slots.values()
        if qid in live_src_ids
    }
    worksheet_ids = {
        qid for slots in slot_map.values() for qid in slots.values()
        if qid not in tombstone_ids
    }
    print(f"  {len(worksheet_ids)} worksheet questions  |  {len(tombstone_ids)} tombstone(s)")
    if tombstone_ids:
        for (skill_c, diff_n), slots in sorted(slot_map.items()):
            ts = [(n, qid) for n, qid in sorted(slots.items()) if qid in tombstone_ids]
            if ts:
                print(f"  Tombstones in {skill_c} {diff_n}: "
                      + ", ".join(f"{n}={qid}" for n, qid in ts))

    # Append new IDs (in bank but not in master)
    new_assignments = []
    for qid, q in rw_questions.items():
        if qid in master_ids:
            continue
        skill_c = _norm_rw_skill(q.get("skill", ""))
        diff_n  = _RW_DIFF_MAP.get(q.get("difficulty", ""), "")
        if not diff_n or skill_c not in WORKSHEET_SKILLS:
            continue
        key = (skill_c, diff_n)
        next_num = max(slot_map[key].keys(), default=0) + 1
        slot_map[key][next_num] = qid
        new_assignments.append((qid, skill_c, diff_n, next_num, f"{skill_c} {diff_n}.{next_num}"))

    fallback_used: list[str] = []
    missing_ids:   list[str] = []
    groups = []
    for (skill, diff_n), slots in sorted(slot_map.items()):
        qs = []
        for q_num in sorted(slots.keys()):
            qid = slots[q_num]
            if qid in tombstone_ids:
                qs.append((q_num, qid, {"_tombstone": True, "correct": sheet_answers.get(qid, "")}))
            else:
                q = rw_questions.get(qid)
                if q is None:
                    q = _image_fallback_question(qid, "rw")
                    if q:
                        fallback_used.append(f"{skill} {diff_n}.{q_num} ({qid})")
                    else:
                        missing_ids.append(f"{skill} {diff_n}.{q_num} ({qid})")
                        continue
                qs.append((q_num, qid, q))
        if qs:
            groups.append((skill, diff_n, qs))

    if fallback_used:
        print(f"  ⚠  Image fallback used for {len(fallback_used)} question(s):")
        for s in fallback_used:
            print(f"       {s}")
    if missing_ids:
        print(f"  ✗  No data or image found for {len(missing_ids)} question(s) — skipped:")
        for s in missing_ids:
            print(f"       {s}")

    if args.skill:
        target = args.skill.strip().lower()
        groups = [g for g in groups if g[0].lower() == target]

    if regen_ids is not None:
        groups = [g for g in groups if any(qid in regen_ids for _, qid, _ in g[2])]

    total = len(groups)
    print(f"Generating {total} RW worksheets with {args.workers} worker(s)...")

    out_dir      = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ak_dir       = out_dir / "_Answers"
    ak_dir.mkdir(parents=True, exist_ok=True)
    font_dir     = _FONT_DIR
    explanations = load_explanations(_RW_EXPL)
    progress:    list = []
    missing_all: list = []

    async def process_fn(page, item):
        skill, diff_n, qs = item
        safe     = skill.replace(",", "")
        subtitle = f"{skill} {diff_n}"
        labelled = [(f"{diff_n}.{q_num}", qid, q) for q_num, qid, q in qs]
        ws_name = f"-{safe} {diff_n}.pdf"
        ak_name = f"-{safe} {diff_n}~Key.pdf"
        if not args.keys_only:
            await render_worksheet(
                page, skill, "", qs, out_dir, font_dir,
                page_title="SAT Reading & Writing",
                page_subtitle=subtitle,
                label_prefix=diff_n,
                pdf_filename=ws_name,
                math_mode=False,
            )
            concept_dir = _CONCEPTS_RW / skill
            concept_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out_dir / ws_name, concept_dir / ws_name)
        if explanations:
            missing = await render_answer_key(
                page, "SAT Reading & Writing", subtitle, labelled, explanations,
                ak_dir, font_dir, ak_name,
                sheet_answers=sheet_answers,
                math_mode=False,
            )
            missing_all.extend(missing)
            ak_concept_dir = _CONCEPTS_RW / "_Answers"
            ak_concept_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ak_dir / ak_name, ak_concept_dir / ak_name)
        return f"{skill} · {diff_n} ({len(qs)} questions)"

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        await asyncio.gather(*[
            _worksheet_worker(browser, chunk, process_fn, progress)
            for chunk in _chunk(groups, args.workers)
        ])
        await browser.close()

    # Write new skill-code assignments CSV
    new_csv = _REPO / "new_rw_skill_codes.csv"
    with open(new_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ID", "Skill", "Difficulty", "Q #", "Skill code"])
        for (qid, sk, di, qn, code) in new_assignments:
            w.writerow([qid, sk, di, qn, code])

    print(f"\nDone! {len(progress)}/{total} worksheets saved to: {out_dir}/")
    if new_assignments:
        print(f"{len(new_assignments)} new IDs assigned → {new_csv}")
    if missing_all:
        print(f"  ⚠  Missing correct answer in JSON for {len(missing_all)} question(s):")
        for s in sorted(missing_all):
            print(f"       {s}")


# ══════════════════════════════════════════════════════════════════════════════
# RUN: PRACTICE TESTS
# ══════════════════════════════════════════════════════════════════════════════

def _test_display(test_key: str) -> str:
    return test_key.upper()


def load_test_data() -> dict:
    """
    Returns:
        {
          'SAT4': {'RW': {1: [qid, ...], 2: [...], 3: [...]}, 'M': {...}},
          'PSAT1': { ... },
          ...
        }
    """
    result = {}

    # SAT4–11 from practice-test-questions.json
    pt_json_path = _PT_JSON if _PT_JSON.exists() else _REPO / _PT_JSON.name
    with open(pt_json_path, encoding="utf-8") as f:
        pt_json = json.load(f)

    _pt_key_map = {"Reading & Writing": "RW", "Math": "M"}
    for pt_label, data in pt_json.items():
        m = re.match(r'Practice Test (\d+)', pt_label)
        if not m:
            continue
        test_key = f"SAT{m.group(1)}"
        if test_key not in _ALL_TESTS:
            continue
        result[test_key] = {}
        for subj_label, modules in data.items():
            subj = _pt_key_map.get(subj_label)
            if subj is None:
                continue
            result[test_key][subj] = {
                mod_num + 1: qids
                for mod_num, qids in enumerate(modules)
            }

    # PSAT1–2 from master spreadsheet
    print("Fetching master spreadsheet for PSAT data…")
    resp = requests.get(QB_DATA_URL, timeout=30)
    resp.raise_for_status()

    psat_slots: dict = defaultdict(lambda: defaultdict(dict))
    for row in csv.DictReader(io.StringIO(resp.text)):
        code = row.get("Skill code", "").strip()
        qid  = row.get("ID", "").strip()
        m = re.match(r'^(PSAT\d+)\s+(RW|M)\s+(\d+)\.(\d+)$', code, re.IGNORECASE)
        if not m:
            continue
        test_key = m.group(1).upper()
        if test_key not in _PSAT_TESTS:
            continue
        subj    = m.group(2).upper()
        mod_num = int(m.group(3))
        q_num   = int(m.group(4))
        psat_slots[test_key][subj][(mod_num, q_num)] = qid

    for test_key, subj_data in psat_slots.items():
        result[test_key] = {}
        for subj, slot_dict in subj_data.items():
            mods: dict = defaultdict(dict)
            for (mod_num, q_num), qid in slot_dict.items():
                mods[mod_num][q_num] = qid
            result[test_key][subj] = {
                mod_num: [slots[q] for q in sorted(slots)]
                for mod_num, slots in sorted(mods.items())
            }

    return result


async def _render_module_pdf(
    page,
    test_key: str,
    subj: str,
    mod_num: int,
    qids: list,
    questions: dict,
    out_dir: Path,
    font_dir: Path,
) -> Path:
    test_display = _test_display(test_key)
    subj_display = "Reading & Writing" if subj == "RW" else "Math"
    subtitle     = f"{subj_display} · Module {mod_num}"
    filename     = f"{test_display} - {subj_display} Module {mod_num}.pdf"
    label_prefix = f"{test_key} {subj} {mod_num}"

    qs = []
    for q_num, qid in enumerate(qids, start=1):
        q = questions.get(qid)
        if q is None:
            continue
        qs.append((q_num, qid, q))

    await render_worksheet(
        page, subj_display, "Easy", qs, out_dir, font_dir,
        page_title=test_display,
        page_subtitle=subtitle,
        label_prefix=label_prefix,
        pdf_filename=filename,
        math_mode=(subj != "RW"),
    )
    return out_dir / filename


async def _practice_test_worker(
    browser: Browser,
    tests: list,
    test_data: dict,
    questions: dict,
    out_dir: Path,
    font_dir: Path,
    progress: list,
) -> None:
    ctx  = await browser.new_context(device_scale_factor=2)
    page = await ctx.new_page()
    try:
        for test_key in tests:
            test_display = _test_display(test_key)
            subj_data    = test_data.get(test_key, {})
            test_dir     = out_dir / test_key
            test_dir.mkdir(parents=True, exist_ok=True)
            module_pdfs  = []

            for subj, mod_num in _MODULE_ORDER:
                qids = subj_data.get(subj, {}).get(mod_num)
                if not qids:
                    continue
                pdf_path = await _render_module_pdf(
                    page, test_key, subj, mod_num, qids, questions, test_dir, font_dir
                )
                module_pdfs.append(pdf_path)

            if module_pdfs:
                writer = PdfWriter()
                for pdf_path in module_pdfs:
                    reader = PdfReader(str(pdf_path))
                    for pg in reader.pages:
                        writer.add_page(pg)
                with open(test_dir / f"{test_key}.pdf", "wb") as f:
                    writer.write(f)

            progress.append(test_key)
            print(f"  [{len(progress)}] {test_display} — {len(module_pdfs)} modules")
    finally:
        await ctx.close()


async def run_practice_tests(args) -> None:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    target_tests = (
        [t.strip().upper() for t in args.tests.split(",")]
        if args.tests
        else _ALL_TESTS
    )

    print("Loading question banks…")
    questions = load_banks()
    print(f"  {len(questions)} questions loaded")

    test_data = load_test_data()
    available = [t for t in target_tests if t in test_data]
    missing   = [t for t in target_tests if t not in test_data]
    if missing:
        print(f"  Warning: no data for {missing}")

    total = len(available)
    print(f"Generating PDFs for {total} test(s) with {args.workers} worker(s)…")

    font_dir = _FONT_DIR
    progress: list = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        await asyncio.gather(*[
            _practice_test_worker(browser, chunk, test_data, questions, out_dir, font_dir, progress)
            for chunk in _chunk(available, args.workers)
        ])
        await browser.close()

    print(f"\nDone! {len(progress)}/{total} tests saved to: {out_dir}/")


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate SAT question images, worksheets, and practice test PDFs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # ── images ──────────────────────────────────────────────────────────────
    p_img = sub.add_parser("images", help="Generate JPG question images (math + RW)")
    p_img.add_argument("--out",     default=str(_REPO / "question-images"),
                       help="Output directory; images go into math/ and rw/ subfolders (default: question-images/)")
    p_img.add_argument("--ids",     help="Comma-separated question IDs to process (default: all)")
    p_img.add_argument("--workers", type=int, default=4,
                       help="Parallel browser contexts (default: 4)")
    p_img.add_argument("--regen-worksheets", action="store_true",
                       help="After generating images, regenerate any worksheets that contain the updated IDs")

    # ── math ────────────────────────────────────────────────────────────────
    p_math = sub.add_parser("math", help="Generate math PDF worksheets")
    p_math.add_argument("--out",       default=str(_REPO / "math-worksheets"),
                        help="Output directory (default: math-worksheets/)")
    p_math.add_argument("--skill",     help='Limit to one skill, e.g. "Linear functions"')
    p_math.add_argument("--workers",   type=int, default=4,
                        help="Parallel browser contexts (default: 4)")
    p_math.add_argument("--keys-only", action="store_true",
                        help="Generate answer keys only (skip worksheet PDFs)")

    # ── rw ──────────────────────────────────────────────────────────────────
    p_rw = sub.add_parser("rw", help="Generate Reading & Writing PDF worksheets")
    p_rw.add_argument("--out",       default=str(_REPO / "rw-worksheets"),
                      help="Output directory (default: rw-worksheets/)")
    p_rw.add_argument("--skill",     help='Limit to one skill, e.g. "Transitions"')
    p_rw.add_argument("--workers",   type=int, default=4,
                      help="Parallel browser contexts (default: 4)")
    p_rw.add_argument("--keys-only", action="store_true",
                      help="Generate answer keys only (skip worksheet PDFs)")

    # ── practice-tests ──────────────────────────────────────────────────────
    p_pt = sub.add_parser("practice-tests", help="Generate practice test PDFs")
    p_pt.add_argument("--out",     default=str(_REPO / "practice-tests"),
                      help="Output directory (default: practice-tests/)")
    p_pt.add_argument("--tests",   help="Comma-separated test keys, e.g. SAT4,SAT5,PSAT1")
    p_pt.add_argument("--workers", type=int, default=4,
                      help="Parallel browser contexts (default: 4)")

    args = parser.parse_args()

    if args.command == "images":
        math_questions = load_math_bank()
        rw_questions = load_rw_bank()
        asyncio.run(run_images(args, math_questions, rw_questions))
    elif args.command == "math":
        questions = load_math_bank()
        asyncio.run(run_math_worksheets(args, questions))
    elif args.command == "rw":
        questions = load_rw_bank()
        asyncio.run(run_rw_worksheets(args, questions))
    elif args.command == "practice-tests":
        asyncio.run(run_practice_tests(args))


if __name__ == "__main__":
    main()
