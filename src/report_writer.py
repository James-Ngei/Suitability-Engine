"""
report_writer.py
----------------
Assembles a publication-quality PDF report from a completed analysis.

Two report depths:
  - summary   : 2 pages — map, stats, classification chart, LLM narrative
  - full      : 4 pages — adds criteria grid, weight chart, methodology section

LLM provider is selected via the LLM_PROVIDER environment variable:

  Provider       Env var(s) needed         Free tier
  ─────────────────────────────────────────────────────────────────
  groq           GROQ_API_KEY              Yes — generous free tier  ← default
  gemini         GEMINI_API_KEY            Yes — 15 req/min free
  anthropic      ANTHROPIC_API_KEY         No  — pay per token
  ollama         OLLAMA_BASE_URL           Yes — local, no key needed
  ─────────────────────────────────────────────────────────────────

Set in your shell or .env:
  export LLM_PROVIDER=groq
  export GROQ_API_KEY=gsk_...

If the call fails for any reason the report still builds using a
deterministic template fallback — the PDF never breaks.

RAG upgrade: pass retrieved context chunks into generate_narrative()
and they get prepended to the prompt. Nothing else changes.

Usage (from api.py):
    from report_writer import build_report

    pdf_path = build_report(
        analysis_id    = analysis_id,
        metadata       = metadata,
        rendered       = rendered,
        config         = CONFIG,
        paths          = PATHS,
        depth          = 'full',         # 'summary' | 'full'
    )

Standalone test:
    python src/report_writer.py
"""

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image,
    Table, TableStyle, PageBreak, HRFlowable,
    KeepTogether,
)
from reportlab.platypus.flowables import Flowable
from reportlab.lib.colors import HexColor


# ── Brand colours (match dashboard) ───────────────────────────────────────────

C_GREEN_DARK   = HexColor('#2d5a1b')
C_GREEN_MID    = HexColor('#3d7a22')
C_GREEN_LIGHT  = HexColor('#a8cc88')
C_GREEN_BG     = HexColor('#f0f7e8')
C_BORDER       = HexColor('#dde5d4')
C_TEXT         = HexColor('#1a2010')
C_TEXT_MUTED   = HexColor('#5a7a42')
C_WHITE        = colors.white

CLASS_COLORS = {
    'highly':     HexColor('#2e7d32'),
    'moderately': HexColor('#66bb6a'),
    'marginally': HexColor('#ffa726'),
    'not':        HexColor('#ef5350'),
    'excluded':   HexColor('#9e9e9e'),
}

PAGE_W, PAGE_H = A4
MARGIN         = 18 * mm
CONTENT_W      = PAGE_W - 2 * MARGIN


# ── Custom flowables ───────────────────────────────────────────────────────────

class ColorRect(Flowable):
    """Solid colour rectangle — used for section header bars."""
    def __init__(self, width, height, fill_color):
        super().__init__()
        self.width  = width
        self.height = height
        self.fill   = fill_color

    def draw(self):
        self.canv.setFillColor(self.fill)
        self.canv.rect(0, 0, self.width, self.height, stroke=0, fill=1)


class HorizontalBar(Flowable):
    """Single coloured bar — used for the classification mini-bars in stats."""
    def __init__(self, width, height, pct, fill_color):
        super().__init__()
        self.width  = width
        self.height = height
        self.pct    = pct
        self.fill   = fill_color

    def draw(self):
        self.canv.setFillColor(HexColor('#eeeeee'))
        self.canv.rect(0, 0, self.width, self.height, stroke=0, fill=1)
        bar_w = self.width * min(self.pct / 100, 1.0)
        if bar_w > 0:
            self.canv.setFillColor(self.fill)
            self.canv.rect(0, 0, bar_w, self.height, stroke=0, fill=1)


# ── Style sheet ────────────────────────────────────────────────────────────────

def _make_styles():
    base = getSampleStyleSheet()

    styles = {
        'cover_title': ParagraphStyle(
            'cover_title',
            fontSize=22, leading=28,
            textColor=C_WHITE, fontName='Helvetica-Bold',
            alignment=TA_LEFT,
        ),
        'cover_sub': ParagraphStyle(
            'cover_sub',
            fontSize=11, leading=16,
            textColor=C_GREEN_LIGHT, fontName='Helvetica',
            alignment=TA_LEFT,
        ),
        'cover_meta': ParagraphStyle(
            'cover_meta',
            fontSize=8, leading=13,
            textColor=C_GREEN_LIGHT, fontName='Helvetica',
            alignment=TA_LEFT,
        ),
        'section_head': ParagraphStyle(
            'section_head',
            fontSize=10, leading=14,
            textColor=C_WHITE, fontName='Helvetica-Bold',
            alignment=TA_LEFT,
            leftIndent=6,
        ),
        'body': ParagraphStyle(
            'body',
            fontSize=9, leading=14,
            textColor=C_TEXT, fontName='Helvetica',
            alignment=TA_JUSTIFY,
            spaceAfter=6,
        ),
        'body_bold': ParagraphStyle(
            'body_bold',
            fontSize=9, leading=14,
            textColor=C_TEXT, fontName='Helvetica-Bold',
            spaceAfter=4,
        ),
        'small': ParagraphStyle(
            'small',
            fontSize=7.5, leading=11,
            textColor=C_TEXT_MUTED, fontName='Helvetica',
            alignment=TA_LEFT,
        ),
        'small_center': ParagraphStyle(
            'small_center',
            fontSize=7.5, leading=11,
            textColor=C_TEXT_MUTED, fontName='Helvetica',
            alignment=TA_CENTER,
        ),
        'label': ParagraphStyle(
            'label',
            fontSize=7, leading=10,
            textColor=C_TEXT_MUTED, fontName='Helvetica-Bold',
            alignment=TA_LEFT,
            spaceAfter=1,
        ),
        'stat_value': ParagraphStyle(
            'stat_value',
            fontSize=18, leading=22,
            textColor=C_GREEN_DARK, fontName='Helvetica-Bold',
            alignment=TA_CENTER,
        ),
        'stat_label': ParagraphStyle(
            'stat_label',
            fontSize=7, leading=10,
            textColor=C_TEXT_MUTED, fontName='Helvetica',
            alignment=TA_CENTER,
        ),
        'caption': ParagraphStyle(
            'caption',
            fontSize=7.5, leading=11,
            textColor=C_TEXT_MUTED, fontName='Helvetica',
            alignment=TA_CENTER,
            spaceBefore=3,
        ),
        'footer': ParagraphStyle(
            'footer',
            fontSize=7, leading=10,
            textColor=C_TEXT_MUTED, fontName='Helvetica',
            alignment=TA_CENTER,
        ),
        'methodology': ParagraphStyle(
            'methodology',
            fontSize=8, leading=13,
            textColor=C_TEXT, fontName='Helvetica',
            alignment=TA_JUSTIFY,
            leftIndent=8, spaceAfter=4,
        ),
    }
    return styles


# ── Page template (header + footer) ───────────────────────────────────────────

def _make_page_template(county_name: str, crop: str, analysis_id: str):
    """Returns on_first_page and on_later_pages callables for SimpleDocTemplate."""

    def _draw_header_footer(canvas, doc, is_cover=False):
        canvas.saveState()

        if not is_cover:
            # Thin green top bar
            canvas.setFillColor(C_GREEN_DARK)
            canvas.rect(0, PAGE_H - 10 * mm, PAGE_W, 10 * mm, stroke=0, fill=1)
            canvas.setFillColor(C_WHITE)
            canvas.setFont('Helvetica-Bold', 8)
            canvas.drawString(MARGIN, PAGE_H - 6.5 * mm,
                              f'{county_name} — {crop} Suitability Analysis')
            canvas.setFont('Helvetica', 7)
            canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 6.5 * mm,
                                   f'GeoFlow  ·  {analysis_id}')

            # Footer line
            canvas.setStrokeColor(C_BORDER)
            canvas.setLineWidth(0.5)
            canvas.line(MARGIN, 12 * mm, PAGE_W - MARGIN, 12 * mm)
            canvas.setFillColor(C_TEXT_MUTED)
            canvas.setFont('Helvetica', 7)
            canvas.drawCentredString(PAGE_W / 2, 8 * mm,
                                     f'Page {doc.page}  ·  Generated {datetime.now().strftime("%d %B %Y")}  ·  For decision-support use only')

        canvas.restoreState()

    def on_first_page(canvas, doc):
        _draw_header_footer(canvas, doc, is_cover=True)

    def on_later_pages(canvas, doc):
        _draw_header_footer(canvas, doc, is_cover=False)

    return on_first_page, on_later_pages


# ── Section heading helper ─────────────────────────────────────────────────────

def _section(title: str, styles: dict) -> list:
    """
    Green bar with white title text overlaid — rendered as a Table cell
    so the background and text occupy the same vertical space.
    """
    bar = Table(
        [[Paragraph(title.upper(), styles['section_head'])]],
        colWidths=[CONTENT_W],
        rowHeights=[8 * mm],
    )
    bar.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), C_GREEN_DARK),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 8),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return [Spacer(1, 5 * mm), bar, Spacer(1, 3 * mm)]


# ── Cover block ────────────────────────────────────────────────────────────────

def _cover_block(metadata: dict, config: dict, styles: dict) -> list:
    """Full-width dark green cover block with title and metadata."""
    county   = config.get('display_name', '')
    crop     = config.get('crop', '')
    country  = config.get('country', '')
    ts       = metadata.get('timestamp', datetime.now().isoformat())
    analysis_id = metadata.get('analysis_id', '')

    try:
        dt = datetime.fromisoformat(ts).strftime('%d %B %Y, %H:%M')
    except Exception:
        dt = ts

    story = []

    # Dark green cover rectangle drawn as a table cell (reportlab trick for bg color)
    cover_data = [[
        Paragraph(
            f'<font size="22"><b>{county}</b></font><br/>'
            f'<font size="13">{crop} Suitability Analysis</font>',
            ParagraphStyle('ct', fontSize=22, leading=30,
                           textColor=C_WHITE, fontName='Helvetica-Bold')
        )
    ]]
    cover_table = Table(cover_data, colWidths=[CONTENT_W])
    cover_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), C_GREEN_DARK),
        ('TOPPADDING',    (0, 0), (-1, -1), 14),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
        ('LEFTPADDING',   (0, 0), (-1, -1), 12),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 12),
        ('ROUNDEDCORNERS', [6]),
    ]))
    story.append(cover_table)
    story.append(Spacer(1, 3 * mm))

    # Meta row
    meta_data = [[
        Paragraph(f'<b>County:</b> {county}, {country}', styles['small']),
        Paragraph(f'<b>Generated:</b> {dt}', styles['small']),
        Paragraph(f'<b>ID:</b> {analysis_id}', styles['small']),
    ]]
    meta_table = Table(meta_data, colWidths=[CONTENT_W / 3] * 3)
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), C_GREEN_BG),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 8),
        ('BOX',  (0, 0), (-1, -1), 0.5, C_BORDER),
        ('GRID', (0, 0), (-1, -1), 0.3, C_BORDER),
    ]))
    story.append(meta_table)
    return story


# ── Score cards ────────────────────────────────────────────────────────────────

def _score_cards(stats: dict, styles: dict) -> Table:
    """
    Single row of 4 stat cards: mean | max | min | std dev.
    Flat layout avoids nested-Table rendering issues in ReportLab Platypus.
    """
    items = [
        ('Mean',    f'{stats.get("mean", 0):.1f}'),
        ('Max',     f'{stats.get("max",  0):.1f}'),
        ('Min',     f'{stats.get("min",  0):.1f}'),
        ('Std dev', f'{stats.get("std",  0):.1f}'),
    ]

    col_w = CONTENT_W / 4 - 0.5 * mm

    # Each cell: value paragraph on top, label below — two rows inside one col
    data_row = []
    for label, value in items:
        cell = [
            Paragraph(value, styles['stat_value']),
            Paragraph(label, styles['stat_label']),
        ]
        data_row.append(cell)

    # Build as a 2-row table per card using a single outer table with
    # explicit row heights so both lines always render
    outer_data = [
        [Paragraph(v, styles['stat_value'])  for _, v in items],
        [Paragraph(l, styles['stat_label'])  for l, _ in items],
    ]

    t = Table(outer_data, colWidths=[col_w] * 4,
              rowHeights=[12 * mm, 5 * mm])
    t.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), C_GREEN_BG),
        ('BOX',           (0, 0), (-1, -1), 0.5, C_BORDER),
        ('LINEAFTER',     (0, 0), (2, -1),  0.3, C_BORDER),
        ('VALIGN',        (0, 0), (-1, 0),  'BOTTOM'),
        ('VALIGN',        (0, 1), (-1, 1),  'TOP'),
        ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING',    (0, 0), (-1, 0),  4),
        ('BOTTOMPADDING', (0, 0), (-1, 0),  0),
        ('TOPPADDING',    (0, 1), (-1, 1),  2),
        ('BOTTOMPADDING', (0, 1), (-1, 1),  6),
        ('LEFTPADDING',   (0, 0), (-1, -1), 2),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 2),
    ]))
    return t


# ── Classification table ───────────────────────────────────────────────────────

def _classification_table(classification: dict, styles: dict,
                          available_width: float = None) -> Table:
    """Compact table: colour swatch | class name | percentage | bar.
    available_width lets callers constrain column widths when the table
    is placed inside another table cell (e.g. beside a chart).
    """
    rows = [
        ('Highly suitable',     'highly',     classification.get('highly_suitable_pct',     0)),
        ('Moderately suitable', 'moderately', classification.get('moderately_suitable_pct', 0)),
        ('Marginally suitable', 'marginally', classification.get('marginally_suitable_pct', 0)),
        ('Not suitable',        'not',        classification.get('not_suitable_pct',        0)),
    ]
    excl = classification.get('excluded_pct', 0)
    if excl > 0.1:
        rows.append(('Excluded / protected', 'excluded', excl))

    total_w = available_width if available_width else CONTENT_W
    BAR_W   = min(40 * mm, total_w * 0.35)
    data   = []
    styles_ts = []

    header = [
        Paragraph('<b>Class</b>', styles['label']),
        Paragraph('<b>% Area</b>', styles['label']),
        Paragraph('<b>Distribution</b>', styles['label']),
    ]
    data.append(header)

    for i, (label, key, pct) in enumerate(rows):
        color  = CLASS_COLORS[key]
        r, g, b = color.red, color.green, color.blue
        hex_str = '#{:02x}{:02x}{:02x}'.format(
            int(r * 255), int(g * 255), int(b * 255))

        label_cell = Paragraph(
            f'<font color="{hex_str}">&#9632;</font>  {label}',
            styles['body']
        )
        pct_cell   = Paragraph(f'<b>{pct:.1f}%</b>', styles['body'])
        bar_cell   = HorizontalBar(BAR_W, 7, pct, color)

        data.append([label_cell, pct_cell, bar_cell])

    col_widths = [total_w - BAR_W - 20 * mm, 20 * mm, BAR_W]
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0),  C_GREEN_BG),
        ('BOX',  (0, 0), (-1, -1), 0.5, C_BORDER),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, C_BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [C_WHITE, HexColor('#fafff7')]),
        ('TOPPADDING',    (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING',   (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    return t


# ── Weights table ──────────────────────────────────────────────────────────────

def _weights_table(weights: dict, config: dict, styles: dict) -> Table:
    """Table showing criterion, weight, and optimal range."""
    criteria_info = config.get('criteria_info', {})

    header = [
        Paragraph('<b>Criterion</b>', styles['label']),
        Paragraph('<b>Weight</b>',    styles['label']),
        Paragraph('<b>Optimal range</b>', styles['label']),
        Paragraph('<b>Description</b>',  styles['label']),
    ]
    data = [header]

    for name, w in weights.items():
        info = criteria_info.get(name, {})
        data.append([
            Paragraph(name.capitalize(), styles['body']),
            Paragraph(f'<b>{w*100:.0f}%</b>', styles['body']),
            Paragraph(info.get('optimal_range', '—'), styles['small']),
            Paragraph(info.get('description',   '—'), styles['small']),
        ])

    col_widths = [28 * mm, 18 * mm, 38 * mm, CONTENT_W - 84 * mm]
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0),  C_GREEN_BG),
        ('BOX',  (0, 0), (-1, -1), 0.5, C_BORDER),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, C_BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [C_WHITE, HexColor('#fafff7')]),
        ('TOPPADDING',    (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING',   (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('GRID', (0, 0), (-1, -1), 0.3, C_BORDER),
    ]))
    return t


# ── Image helper ───────────────────────────────────────────────────────────────

def _img(path: Optional[Path], max_width: float, max_height: float) -> Optional[Image]:
    """Load an image, scale to fit within max_width × max_height, preserve AR."""
    if not path or not Path(path).exists():
        return None
    img = Image(str(path))
    iw, ih = img.imageWidth, img.imageHeight
    if iw == 0 or ih == 0:
        return None
    scale = min(max_width / iw, max_height / ih)
    img.drawWidth  = iw * scale
    img.drawHeight = ih * scale
    return img


# ── LLM provider layer ────────────────────────────────────────────────────────
#
# Switch provider with one env var — no code changes needed.
# Priority order when LLM_PROVIDER is not set: groq → gemini → anthropic → ollama
#

def _build_prompt(stats: dict, classification: dict,
                  weights: dict, config: dict,
                  rag_context: str = '') -> str:
    """
    Build the narrative prompt. If rag_context is supplied (future RAG upgrade),
    it is prepended as grounding material before the analysis facts.
    """
    county        = config.get('display_name', 'the county')
    crop          = config.get('crop', 'the crop')
    top_criterion = max(weights, key=weights.get) if weights else 'rainfall'
    highly_pct    = classification.get('highly_suitable_pct', 0)
    mod_pct       = classification.get('moderately_suitable_pct', 0)
    suitable_pct  = highly_pct + mod_pct

    context_block = ''
    if rag_context:
        context_block = (
            f'Reference methodology (use to ground your response):\n'
            f'{rag_context.strip()}\n\n'
        )

    return (
        f'{context_block}'
        f'You are a GIS analyst writing a professional report section for a '
        f'county government officer. Write in plain, professional English. '
        f'No bullet points. No headers. No markdown. '
        f'Keep each paragraph to 3-4 sentences. Be specific and actionable.\n\n'
        f'Analysis context:\n'
        f'- Location: {county}, Kenya\n'
        f'- Crop: {crop}\n'
        f'- Mean suitability score: {stats.get("mean", 0):.1f} / 100\n'
        f'- Highly suitable area: {highly_pct:.1f}%\n'
        f'- Moderately suitable area: {mod_pct:.1f}%\n'
        f'- Combined suitable area (>=50): {suitable_pct:.1f}%\n'
        f'- Most influential criterion: {top_criterion} '
        f'({weights.get(top_criterion, 0)*100:.0f}% weight)\n'
        f'- Score range: {stats.get("min", 0):.1f} to {stats.get("max", 0):.1f}\n\n'
        f'Write exactly 3 paragraphs:\n'
        f'1. Overall suitability assessment — what the scores mean for '
        f'{crop} farming in {county}.\n'
        f'2. Spatial interpretation — which areas are most/least suitable '
        f'and likely why, referencing the top criterion.\n'
        f'3. Recommendations — what a county agriculture officer should '
        f'consider next, including data limitations.'
    )


def _call_groq(prompt: str) -> str:
    from groq import Groq
    client = Groq(api_key=os.environ['GROQ_API_KEY'])
    resp = client.chat.completions.create(
        model=os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile'),
        max_tokens=600,
        messages=[{'role': 'user', 'content': prompt}],
    )
    return resp.choices[0].message.content.strip()


def _call_gemini(prompt: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=os.environ['GEMINI_API_KEY'])
    model = genai.GenerativeModel(
        os.environ.get('GEMINI_MODEL', 'gemini-1.5-flash')
    )
    return model.generate_content(prompt).text.strip()


def _call_anthropic(prompt: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])
    msg = client.messages.create(
        model=os.environ.get('ANTHROPIC_MODEL', 'claude-haiku-4-5-20251001'),
        max_tokens=600,
        messages=[{'role': 'user', 'content': prompt}],
    )
    return msg.content[0].text.strip()


def _call_ollama(prompt: str) -> str:
    import urllib.request, json as _json
    base = os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434')
    model = os.environ.get('OLLAMA_MODEL', 'llama3.2')
    payload = _json.dumps({
        'model':  model,
        'prompt': prompt,
        'stream': False,
    }).encode()
    req  = urllib.request.Request(
        f'{base}/api/generate',
        data=payload,
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return _json.loads(r.read())['response'].strip()


# Provider registry — tried in this order when auto-detecting
_PROVIDERS = {
    'groq':      (_call_groq,      'GROQ_API_KEY'),
    'gemini':    (_call_gemini,    'GEMINI_API_KEY'),
    'anthropic': (_call_anthropic, 'ANTHROPIC_API_KEY'),
    'ollama':    (_call_ollama,    None),             # no key needed
}


def _call_llm(prompt: str) -> str:
    """
    Route to the correct LLM provider.

    Explicit:  set LLM_PROVIDER=groq (or gemini / anthropic / ollama)
    Auto:      tries groq → gemini → anthropic → ollama, uses first with a key set.
    """
    explicit = os.environ.get('LLM_PROVIDER', '').lower().strip()

    if explicit:
        if explicit not in _PROVIDERS:
            raise ValueError(
                f"Unknown LLM_PROVIDER '{explicit}'. "
                f"Choose from: {list(_PROVIDERS)}"
            )
        fn, key_var = _PROVIDERS[explicit]
        if key_var and not os.environ.get(key_var):
            raise EnvironmentError(
                f"LLM_PROVIDER={explicit} but {key_var} is not set."
            )
        return fn(prompt)

    # Auto-detect: first provider whose key is present
    for name, (fn, key_var) in _PROVIDERS.items():
        if key_var is None or os.environ.get(key_var):
            print(f'  ℹ️  LLM provider auto-selected: {name}')
            return fn(prompt)

    raise EnvironmentError(
        'No LLM provider available. Set one of: '
        'GROQ_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY, '
        'or run Ollama locally.'
    )


def _narrative_fallback(stats: dict, classification: dict,
                         weights: dict, config: dict) -> str:
    """Deterministic template — used when every LLM call fails."""
    county       = config.get('display_name', 'the study area')
    crop         = config.get('crop', 'the crop')
    highly_pct   = classification.get('highly_suitable_pct', 0)
    mod_pct      = classification.get('moderately_suitable_pct', 0)
    suitable_pct = highly_pct + mod_pct
    mean_score   = stats.get('mean', 0)
    top          = max(weights, key=weights.get) if weights else 'rainfall'

    return (
        f'The suitability analysis for {crop} cultivation in {county} yields '
        f'a mean score of {mean_score:.1f} out of 100, indicating '
        f'{"favourable" if mean_score >= 55 else "mixed"} conditions across '
        f'the study area. Approximately {suitable_pct:.1f}% of the analysed '
        f'land falls within the suitable categories (score \u2265 50), with '
        f'{highly_pct:.1f}% classified as highly suitable.\n\n'
        f'Spatial variation across {county} reflects differences in the '
        f'weighted criteria, particularly {top}, which carries the greatest '
        f'influence in the analysis. Areas scoring below 30 are predominantly '
        f'constrained by one or more limiting factors identified in the '
        f'criterion layers.\n\n'
        f'County agriculture officers are advised to use these results as an '
        f'initial screening tool for {crop} programme planning. '
        f'Ground-truthing of highly suitable zones is recommended before '
        f'resource allocation. Data limitations include the spatial resolution '
        f'of source datasets and the assumption of uniform weights across '
        f'sub-county boundaries.'
    )


# ── Public narrative function ──────────────────────────────────────────────────

def generate_narrative(stats: dict, classification: dict,
                        weights: dict, config: dict,
                        rag_context: str = '') -> str:
    """
    Generate a 3-paragraph interpretive narrative for the report.

    Provider is selected via LLM_PROVIDER env var (see module docstring).
    Always returns a string — falls back to template if all providers fail.

    Args:
        stats:          Statistics dict from the analysis.
        classification: Classification percentages dict.
        weights:        Criterion weights used.
        config:         Active county config.
        rag_context:    Optional retrieved methodology text (RAG upgrade slot).
                        Pass non-empty string to ground the LLM in real sources.

    Returns:
        Plain text narrative (paragraph breaks as \\n\\n).
    """
    prompt = _build_prompt(stats, classification, weights, config, rag_context)

    try:
        text = _call_llm(prompt)
        print('  ✅ LLM narrative generated successfully')
        return text
    except Exception as e:
        print(f'  ⚠️  LLM narrative failed ({type(e).__name__}: {e})')
        print('      Using deterministic template fallback.')
        return _narrative_fallback(stats, classification, weights, config)


# ── Main builder ───────────────────────────────────────────────────────────────

def build_report(
    analysis_id: str,
    metadata: dict,
    rendered: dict,
    config: dict,
    paths: dict,
    depth: str = 'full',
) -> Path:
    """
    Build the PDF report and save it alongside the analysis GeoTIFF.

    Args:
        analysis_id:  ID string.
        metadata:     The metadata dict saved by /analyze.
        rendered:     {asset_name: Path} from render_all().
        config:       Active county config.
        paths:        _paths dict from config.
        depth:        'summary' (2 pages) or 'full' (4 pages).

    Returns:
        Path to the saved PDF.
    """
    output_path = paths['api_results_dir'] / f'report_{analysis_id}.pdf'
    styles      = _make_styles()

    county_name = config.get('display_name', '')
    crop        = config.get('crop', '')
    stats       = metadata.get('statistics', {})
    classif     = metadata.get('classification', {})
    weights     = metadata.get('weights', config.get('weights', {}))

    on_first, on_later = _make_page_template(county_name, crop, analysis_id)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN + 10 * mm,     # room for header bar
        bottomMargin=MARGIN + 6 * mm,   # room for footer
        title=f'{county_name} {crop} Suitability Report',
        author='GeoFlow Analysis Engine',
        subject=f'Multi-criteria suitability analysis — {analysis_id}',
    )

    story = []

    # ── PAGE 1: Cover + map + stats ────────────────────────────────────────────

    story += _cover_block(metadata, config, styles)
    story.append(Spacer(1, 4 * mm))

    # Suitability map
    story += _section('Suitability Map', styles)
    suit_map = _img(rendered.get('suitability_map'), CONTENT_W, 110 * mm)
    if suit_map:
        story.append(suit_map)
        story.append(Paragraph(
            f'Figure 1. {crop} suitability across {county_name}. '
            f'Four-class weighted overlay using {len(weights)} biophysical criteria. '
            f'Dashed line = county boundary.',
            styles['caption']
        ))
    else:
        story.append(Paragraph('Map image not available.', styles['small']))

    story.append(Spacer(1, 4 * mm))

    # Score summary cards — always full width, never nested in a side table
    story += _section('Results Summary', styles)

    story.append(_score_cards(stats, styles))
    story.append(Spacer(1, 3 * mm))

    # Chart + classification table side by side
    chart_w    = CONTENT_W * 0.46
    classif_w  = CONTENT_W * 0.54
    suit_chart = _img(rendered.get('classification_chart'), chart_w, 50 * mm)
    classif_t  = _classification_table(classif, styles,
                                        available_width=classif_w - 4 * mm)

    if suit_chart:
        side_data  = [[suit_chart, classif_t]]
        side_table = Table(side_data, colWidths=[chart_w, classif_w])
        side_table.setStyle(TableStyle([
            ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING',  (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ]))
        story.append(side_table)
    else:
        story.append(_classification_table(classif, styles))

    # ── Narrative + weights — flow naturally after results summary ───────────

    story += _section('Interpretation', styles)

    narrative = generate_narrative(stats, classif, weights, config,
                                   rag_context=metadata.get("rag_context", ""))
    for para in narrative.split('\n\n'):
        para = para.strip()
        if para:
            story.append(Paragraph(para, styles['body']))

    story.append(Spacer(1, 4 * mm))
    story += _section('Criterion Weights Used', styles)
    story.append(_weights_table(weights, config, styles))

    weight_chart = _img(rendered.get('weight_chart'), CONTENT_W * 0.6, 55 * mm)
    if weight_chart:
        story.append(Spacer(1, 3 * mm))
        story.append(weight_chart)
        story.append(Paragraph(
            'Figure 2. Relative importance of each criterion in the weighted overlay.',
            styles['caption']
        ))

    # ── PAGES 3–4: Full depth only ─────────────────────────────────────────────

    if depth == 'full':

        # Criteria grid
        story.append(PageBreak())
        story += _section('Individual Criterion Layers', styles)
        story.append(Paragraph(
            'Each layer shows the normalised suitability score (0–100) for that criterion '
            'alone, before weighting. This helps identify which specific factor limits or '
            'enables suitability in each part of the county.',
            styles['body']
        ))
        story.append(Spacer(1, 3 * mm))

        grid = _img(rendered.get('criteria_grid'), CONTENT_W, 160 * mm)
        if grid:
            story.append(grid)
            story.append(Paragraph(
                'Figure 3. Normalised individual criterion scores (0–100). '
                'Colormaps are independent per layer; do not compare shades across panels.',
                styles['caption']
            ))

        # Methodology
        story.append(PageBreak())
        story += _section('Methodology', styles)

        norm_config = config.get('normalization', {})
        story.append(Paragraph(
            'This analysis uses a Multi-Criteria Decision Analysis (MCDA) weighted overlay '
            'approach. Each biophysical layer is normalised to a 0–100 suitability score '
            'using a fuzzy membership function, then combined using analyst-defined weights '
            'that sum to 1.0. The final score represents a weighted average of criterion '
            'suitability across the landscape.',
            styles['body']
        ))

        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph('Normalisation functions applied:', styles['body_bold']))

        for name, norm in norm_config.items():
            fn_type = norm.get('type', '')
            params  = norm.get('params', {})
            desc    = norm.get('description', '')
            param_str = '  ·  '.join(f'{k} = {v}' for k, v in params.items())
            story.append(Paragraph(
                f'<b>{name.capitalize()}</b> ({fn_type}): {desc}<br/>'
                f'<font color="#7a8f68">{param_str}</font>',
                styles['methodology']
            ))

        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph('Classification thresholds:', styles['body_bold']))
        thresholds = [
            ('Highly suitable',     '70 – 100'),
            ('Moderately suitable', '50 – 70'),
            ('Marginally suitable', '30 – 50'),
            ('Not suitable',        '0 – 30'),
        ]
        for label, rng in thresholds:
            story.append(Paragraph(f'<b>{label}:</b>  score {rng}', styles['methodology']))

        story.append(Spacer(1, 4 * mm))
        story += _section('Data Sources & Limitations', styles)
        story.append(Paragraph(
            'Input layers were sourced from publicly available global datasets '
            '(SRTM elevation, CHIRPS rainfall, WorldClim temperature, SoilGrids clay content). '
            'Slope was derived from the DEM. Protected area constraints were applied where '
            'available. All layers were resampled to a common pixel grid before analysis.',
            styles['body']
        ))
        story.append(Paragraph(
            'Limitations: spatial resolution of source data (250 m – 1 km) may not capture '
            'fine-scale variation within sub-county units. Temporal averages mask seasonal '
            'variability. Results represent biophysical potential only and do not account for '
            'socioeconomic factors, market access, or current land use. Field validation is '
            'recommended before operational decisions are made.',
            styles['body']
        ))
        story.append(Paragraph(
            'This report is generated for decision-support purposes only and should not be '
            'used as a substitute for professional agronomic assessment.',
            styles['small']
        ))

    doc.build(story, onFirstPage=on_first, onLaterPages=on_later)
    print(f'  ✅ Report saved: {output_path.name}  ({depth})')
    return output_path


# ── API hook (add to api.py) ───────────────────────────────────────────────────
#
# 1. Import at top of api.py:
#        from report_writer import build_report
#
# 2. New endpoint:
#
# @app.post("/report/{analysis_id}")
# async def generate_report(analysis_id: str, depth: str = 'full'):
#     """Generate a PDF report for a completed analysis."""
#     meta_path = PATHS['api_results_dir'] / f'metadata_{analysis_id}.json'
#     if not meta_path.exists():
#         raise HTTPException(status_code=404, detail='Analysis not found')
#
#     with open(meta_path) as f:
#         metadata = json.load(f)
#
#     rendered = metadata.get('rendered_assets', {})
#     rendered = {k: Path(v) for k, v in rendered.items()}
#
#     pdf_path = build_report(
#         analysis_id = analysis_id,
#         metadata    = metadata,
#         rendered    = rendered,
#         config      = CONFIG,
#         paths       = PATHS,
#         depth       = depth,
#     )
#
#     county_slug = CONFIG['county']
#     return FileResponse(
#         path       = str(pdf_path),
#         media_type = 'application/pdf',
#         filename   = f'{county_slug}_suitability_{analysis_id}.pdf',
#     )


# ── Standalone test ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    sys.path.append(str(Path(__file__).parent))

    # Dummy data so you can test layout without a real analysis
    dummy_metadata = {
        'analysis_id':   'test_20250101_120000',
        'county':        'kitui',
        'timestamp':     datetime.now().isoformat(),
        'statistics':    {'mean': 54.2, 'max': 91.3, 'min': 3.1, 'std': 18.7, 'median': 56.1},
        'classification': {
            'highly_suitable_pct':     22.4,
            'moderately_suitable_pct': 35.1,
            'marginally_suitable_pct': 18.9,
            'not_suitable_pct':         8.3,
            'excluded_pct':            15.3,
        },
        'weights': {
            'rainfall':    0.30,
            'elevation':   0.15,
            'temperature': 0.20,
            'soil':        0.20,
            'slope':       0.15,
        },
        'constraints_applied': True,
    }

    dummy_config = {
        'county':       'kitui',
        'display_name': 'Kitui County',
        'country':      'Kenya',
        'crop':         'Cotton',
        'weights':      dummy_metadata['weights'],
        'criteria_info': {
            'rainfall':    {'description': 'Annual rainfall mm/yr',  'optimal_range': '600–900 mm'},
            'elevation':   {'description': 'Elevation above sea level', 'optimal_range': '400–1000 m'},
            'temperature': {'description': 'Mean annual temperature', 'optimal_range': '22–32°C'},
            'soil':        {'description': 'Soil clay content g/kg',  'optimal_range': '200–400 g/kg'},
            'slope':       {'description': 'Terrain slope degrees',   'optimal_range': '0–5° (max 15°)'},
        },
        'normalization': {
            'rainfall':    {'type': 'trapezoidal', 'params': {'a':400,'b':600,'c':900,'d':1200},  'description': 'Semi-arid 600-900mm optimal'},
            'elevation':   {'type': 'trapezoidal', 'params': {'a':200,'b':400,'c':1000,'d':1500}, 'description': 'Lowland cotton 400-1000m optimal'},
            'temperature': {'type': 'gaussian',    'params': {'optimal':27,'spread':5},            'description': 'Warmer optimum for ASAL cotton'},
            'soil':        {'type': 'trapezoidal', 'params': {'a':100,'b':200,'c':400,'d':550},   'description': 'Moderate clay 200-400 optimal'},
            'slope':       {'type': 'linear_descending', 'params': {'min_val':0,'max_val':15},    'description': 'Flat land best, zero above 15°'},
        },
    }

    out_dir = Path('/tmp/geoflow_test')
    out_dir.mkdir(exist_ok=True)

    dummy_paths = {
        'api_results_dir': out_dir,
        'boundary': Path('/nonexistent'),   # will be skipped gracefully
    }

    # No rendered assets in dummy run — report handles missing images gracefully
    pdf = build_report(
        analysis_id = 'test_20250101_120000',
        metadata    = dummy_metadata,
        rendered    = {},
        config      = dummy_config,
        paths       = dummy_paths,
        depth       = 'full',
    )
    print(f'\nTest report: {pdf}')
    print('Open it to check layout before connecting real analysis data.')