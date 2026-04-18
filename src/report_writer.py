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

Set in .env at project root (auto-loaded at startup):
  LLM_PROVIDER=groq
  GROQ_API_KEY=gsk_...

RAG support: call inject_rag_context() at startup to build the vector store
from agronomic PDFs in data/rag_docs/. The narrative prompt is automatically
enriched with retrieved passages.

Usage (from api.py):
    from report_writer import build_report

    pdf_path = build_report(
        analysis_id    = analysis_id,
        metadata       = metadata,
        rendered       = rendered,
        config         = CONFIG,
        paths          = PATHS,
        depth          = 'full',
    )
"""

import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("report-writer")

# ── Load .env early so keys are available before anything else ─────────────────
def _load_dotenv():
    """Load .env from project root. Silent if missing — prod uses real env vars."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key   = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:   # don't override real env vars
                os.environ[key] = value

_load_dotenv()


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


# ── Brand colours ──────────────────────────────────────────────────────────────
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


# ══════════════════════════════════════════════════════════════════════════════
# RAG — Vector store for agronomic context retrieval
# ══════════════════════════════════════════════════════════════════════════════

# Module-level RAG store — built once at startup, reused for every report
_RAG_STORE = None
_RAG_AVAILABLE = False


def _get_rag_docs_dir() -> Path:
    """Return the directory where agronomic source PDFs/txts live."""
    return Path(__file__).resolve().parent.parent / "data" / "rag_docs"


def build_rag_store(docs_dir: Optional[Path] = None) -> bool:
    """
    Build an in-memory vector store from agronomic documents.

    Supported formats: .txt, .md, .pdf (text-extractable)
    Place documents in data/rag_docs/ — e.g.:
      - fao_cotton_growing_guide.txt
      - kenya_cotton_best_practices.md
      - soilgrids_methodology.txt

    Called once at API startup. Returns True if store was built successfully.
    """
    global _RAG_STORE, _RAG_AVAILABLE

    if docs_dir is None:
        docs_dir = _get_rag_docs_dir()

    docs_dir = Path(docs_dir)
    if not docs_dir.exists():
        docs_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"RAG docs dir created: {docs_dir}  (add .txt/.md docs to enable RAG)")
        return False

    doc_files = list(docs_dir.glob("*.txt")) + list(docs_dir.glob("*.md"))

    # Try PDF extraction if PyMuPDF available
    try:
        import fitz  # PyMuPDF
        doc_files += list(docs_dir.glob("*.pdf"))
    except ImportError:
        pass

    if not doc_files:
        logger.info(f"RAG: no documents found in {docs_dir} — RAG disabled")
        return False

    # Try ChromaDB first, fall back to simple TF-IDF store
    try:
        _build_chroma_store(doc_files)
        _RAG_AVAILABLE = True
        logger.info(f"RAG: ChromaDB store built from {len(doc_files)} documents")
        return True
    except ImportError:
        pass

    try:
        _build_tfidf_store(doc_files)
        _RAG_AVAILABLE = True
        logger.info(f"RAG: TF-IDF store built from {len(doc_files)} documents")
        return True
    except Exception as e:
        logger.warning(f"RAG: could not build store — {e}")
        return False


def _read_doc(path: Path) -> str:
    """Extract text from .txt, .md, or .pdf."""
    if path.suffix == ".pdf":
        try:
            import fitz
            doc = fitz.open(str(path))
            return "\n".join(page.get_text() for page in doc)
        except Exception:
            return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _chunk_text(text: str, chunk_size: int = 400, overlap: int = 80) -> list:
    """Split text into overlapping word chunks."""
    words  = text.split()
    chunks = []
    i      = 0
    while i < len(words):
        chunk = " ".join(words[i : i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
        i += chunk_size - overlap
    return chunks


def _build_chroma_store(doc_files: list):
    """Build ChromaDB in-memory collection."""
    global _RAG_STORE
    import chromadb
    from chromadb.utils import embedding_functions

    client     = chromadb.Client()
    collection = client.get_or_create_collection(
        name="agro_docs",
        embedding_function=embedding_functions.DefaultEmbeddingFunction(),
    )

    ids, docs, metas = [], [], []
    for doc_path in doc_files:
        text   = _read_doc(doc_path)
        chunks = _chunk_text(text)
        for i, chunk in enumerate(chunks):
            ids.append(f"{doc_path.stem}_{i}")
            docs.append(chunk)
            metas.append({"source": doc_path.name})

    if ids:
        collection.add(documents=docs, ids=ids, metadatas=metas)

    _RAG_STORE = {"type": "chroma", "collection": collection}


def _build_tfidf_store(doc_files: list):
    """Fallback: simple TF-IDF in-memory store using sklearn."""
    global _RAG_STORE
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np

    all_chunks = []
    sources    = []
    for doc_path in doc_files:
        text   = _read_doc(doc_path)
        chunks = _chunk_text(text)
        all_chunks.extend(chunks)
        sources.extend([doc_path.name] * len(chunks))

    vectorizer = TfidfVectorizer(stop_words="english", max_features=8000)
    matrix     = vectorizer.fit_transform(all_chunks)

    _RAG_STORE = {
        "type":       "tfidf",
        "chunks":     all_chunks,
        "sources":    sources,
        "vectorizer": vectorizer,
        "matrix":     matrix,
    }


def retrieve_context(query: str, n_results: int = 4) -> str:
    """
    Retrieve the most relevant agronomic passages for a query.
    Returns empty string if RAG is not available.
    """
    if not _RAG_AVAILABLE or _RAG_STORE is None:
        return ""

    try:
        if _RAG_STORE["type"] == "chroma":
            results = _RAG_STORE["collection"].query(
                query_texts=[query], n_results=n_results
            )
            passages = results["documents"][0]
            sources  = [m["source"] for m in results["metadatas"][0]]

        elif _RAG_STORE["type"] == "tfidf":
            from sklearn.metrics.pairwise import cosine_similarity
            vec    = _RAG_STORE["vectorizer"].transform([query])
            scores = cosine_similarity(vec, _RAG_STORE["matrix"])[0]
            top    = scores.argsort()[-n_results:][::-1]
            passages = [_RAG_STORE["chunks"][i] for i in top]
            sources  = [_RAG_STORE["sources"][i] for i in top]

        if not passages:
            return ""

        context = "Relevant agronomic knowledge:\n"
        for passage, source in zip(passages, sources):
            context += f"\n[{source}]\n{passage.strip()}\n"
        return context

    except Exception as e:
        logger.warning(f"RAG retrieval failed: {e}")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# LLM provider layer
# ══════════════════════════════════════════════════════════════════════════════

def _build_prompt(stats: dict, classification: dict,
                  weights: dict, config: dict,
                  rag_context: str = '') -> str:
    county        = config.get('display_name', 'the county')
    crop          = config.get('crop', 'the crop')
    top_criterion = max(weights, key=weights.get) if weights else 'rainfall'
    highly_pct    = classification.get('highly_suitable_pct', 0)
    mod_pct       = classification.get('moderately_suitable_pct', 0)
    suitable_pct  = highly_pct + mod_pct
    criteria_info = config.get('criteria_info', {})

    # Build criteria context from config
    criteria_lines = []
    for name, w in weights.items():
        info    = criteria_info.get(name, {})
        optimal = info.get('optimal_range', '')
        criteria_lines.append(
            f"  - {name.capitalize()} (weight {w*100:.0f}%): optimal {optimal}"
        )
    criteria_str = "\n".join(criteria_lines)

    context_block = ''
    if rag_context:
        context_block = (
            f"Reference knowledge (use to ground your interpretation):\n"
            f"{rag_context.strip()}\n\n"
            f"{'─' * 60}\n\n"
        )

    return (
        f"{context_block}"
        f"You are a senior GIS analyst writing the interpretation section of a "
        f"formal crop suitability report for {county} County government officers "
        f"and agricultural extension workers.\n\n"
        f"Write in clear, professional English. No bullet points. No markdown. "
        f"No headers. Each paragraph should be 4-5 sentences and directly "
        f"reference the specific data values provided. Be concrete and actionable — "
        f"name specific areas, specific thresholds, and specific recommendations "
        f"relevant to {crop} cultivation in this region of Kenya.\n\n"
        f"ANALYSIS DATA:\n"
        f"- Location: {county}, Kenya\n"
        f"- Crop: {crop}\n"
        f"- Mean suitability score: {stats.get('mean', 0):.1f} / 100\n"
        f"- Score range: {stats.get('min', 0):.1f} to {stats.get('max', 0):.1f} "
        f"  (std dev: {stats.get('std', 0):.1f})\n"
        f"- Highly suitable (≥70): {highly_pct:.1f}% of county area\n"
        f"- Moderately suitable (50-70): {mod_pct:.1f}%\n"
        f"- Marginally suitable (30-50): {classification.get('marginally_suitable_pct', 0):.1f}%\n"
        f"- Not suitable (<30): {classification.get('not_suitable_pct', 0):.1f}%\n"
        f"- Excluded (protected): {classification.get('excluded_pct', 0):.1f}%\n"
        f"- Most influential criterion: {top_criterion} ({weights.get(top_criterion, 0)*100:.0f}% weight)\n\n"
        f"CRITERIA USED:\n{criteria_str}\n\n"
        f"Write exactly 3 paragraphs:\n\n"
        f"Paragraph 1 — OVERALL ASSESSMENT: Interpret what a mean score of "
        f"{stats.get('mean', 0):.1f}/100 means for {crop} viability in {county}. "
        f"Reference the {suitable_pct:.1f}% suitable area figure and explain "
        f"what this implies for county-level agricultural planning. Compare to "
        f"known {crop} growing requirements.\n\n"
        f"Paragraph 2 — SPATIAL INTERPRETATION: Explain the spatial distribution "
        f"— why {top_criterion} is the dominant driver, what landscapes or "
        f"sub-regions in {county} are likely to correspond to the highly suitable "
        f"zones, and what constraints limit the {classification.get('not_suitable_pct', 0):.1f}% "
        f"not-suitable areas. Reference the actual threshold values in the criteria.\n\n"
        f"Paragraph 3 — RECOMMENDATIONS: Provide 3-4 specific, actionable steps "
        f"for county agriculture officers — including prioritisation of the highly "
        f"suitable zones, data quality caveats to communicate to farmers, and what "
        f"complementary field assessments should follow this desk analysis."
    )


def _call_groq(prompt: str) -> str:
    from groq import Groq
    api_key = os.environ.get('GROQ_API_KEY')
    if not api_key:
        raise EnvironmentError("GROQ_API_KEY not set")
    client = Groq(api_key=api_key)
    resp   = client.chat.completions.create(
        model=os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile'),
        max_tokens=800,
        temperature=0.65,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a GIS and agricultural analyst. Write formal, "
                    "data-driven report sections. Never use bullet points, "
                    "headers, or markdown. Write flowing professional prose only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
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
    msg    = client.messages.create(
        model=os.environ.get('ANTHROPIC_MODEL', 'claude-haiku-4-5-20251001'),
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _call_ollama(prompt: str) -> str:
    import urllib.request, json as _json
    base    = os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434')
    model   = os.environ.get('OLLAMA_MODEL', 'llama3.2')
    payload = _json.dumps({'model': model, 'prompt': prompt, 'stream': False}).encode()
    req     = urllib.request.Request(
        f'{base}/api/generate', data=payload,
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return _json.loads(r.read())['response'].strip()


_PROVIDERS = {
    'groq':      (_call_groq,      'GROQ_API_KEY'),
    'gemini':    (_call_gemini,    'GEMINI_API_KEY'),
    'anthropic': (_call_anthropic, 'ANTHROPIC_API_KEY'),
    'ollama':    (_call_ollama,    None),
}


def _call_llm(prompt: str) -> str:
    """Route to correct LLM provider with explicit error messages."""
    explicit = os.environ.get('LLM_PROVIDER', '').lower().strip()

    if explicit:
        if explicit not in _PROVIDERS:
            raise ValueError(f"Unknown LLM_PROVIDER '{explicit}'. Choose from: {list(_PROVIDERS)}")
        fn, key_var = _PROVIDERS[explicit]
        if key_var and not os.environ.get(key_var):
            raise EnvironmentError(
                f"LLM_PROVIDER={explicit} but {key_var} is not set. "
                f"Add {key_var}=your_key to your .env file."
            )
        return fn(prompt)

    # Auto-detect: first provider whose key is present
    for name, (fn, key_var) in _PROVIDERS.items():
        if key_var is None or os.environ.get(key_var):
            logger.info(f"LLM provider auto-selected: {name}")
            return fn(prompt)

    raise EnvironmentError(
        "No LLM provider available. "
        "Add GROQ_API_KEY=gsk_... to your .env file at the project root. "
        "Get a free key at console.groq.com"
    )


def _narrative_fallback(stats: dict, classification: dict,
                         weights: dict, config: dict) -> str:
    """Deterministic template — only fires when ALL LLM providers fail."""
    county       = config.get('display_name', 'the study area')
    crop         = config.get('crop', 'the crop')
    highly_pct   = classification.get('highly_suitable_pct', 0)
    mod_pct      = classification.get('moderately_suitable_pct', 0)
    suitable_pct = highly_pct + mod_pct
    mean_score   = stats.get('mean', 0)
    top          = max(weights, key=weights.get) if weights else 'rainfall'

    return (
        f"The suitability analysis for {crop} cultivation in {county} yields "
        f"a mean score of {mean_score:.1f} out of 100, indicating "
        f"{'favourable' if mean_score >= 55 else 'mixed'} conditions across "
        f"the study area. Approximately {suitable_pct:.1f}% of the analysed "
        f"land falls within the suitable categories (score \u2265 50), with "
        f"{highly_pct:.1f}% classified as highly suitable.\n\n"
        f"Spatial variation across {county} reflects differences in the "
        f"weighted criteria, particularly {top}, which carries the greatest "
        f"influence in the analysis. Areas scoring below 30 are predominantly "
        f"constrained by one or more limiting factors identified in the "
        f"criterion layers.\n\n"
        f"County agriculture officers are advised to use these results as an "
        f"initial screening tool for {crop} programme planning. "
        f"Ground-truthing of highly suitable zones is recommended before "
        f"resource allocation. Data limitations include the spatial resolution "
        f"of source datasets and the assumption of uniform weights across "
        f"sub-county boundaries.\n\n"
        f"[NOTE: This narrative was generated from a template because no LLM "
        f"provider was available. Add GROQ_API_KEY to your .env file to enable "
        f"AI-generated interpretation.]"
    )


def generate_narrative(stats: dict, classification: dict,
                        weights: dict, config: dict,
                        rag_context: str = '') -> str:
    """
    Generate a 3-paragraph interpretive narrative.
    Auto-retrieves RAG context if store is available and rag_context not supplied.
    Always returns a string — falls back to template if all providers fail.
    """
    # Auto-retrieve RAG context if not supplied
    if not rag_context and _RAG_AVAILABLE:
        county = config.get('display_name', '')
        crop   = config.get('crop', '')
        query  = f"{crop} cultivation suitability {county} rainfall soil temperature Kenya"
        rag_context = retrieve_context(query)
        if rag_context:
            logger.info(f"RAG: retrieved context for narrative ({len(rag_context)} chars)")

    prompt = _build_prompt(stats, classification, weights, config, rag_context)

    try:
        text = _call_llm(prompt)
        logger.info("LLM narrative generated successfully")
        return text
    except EnvironmentError as e:
        logger.error(f"LLM config error: {e}")
        logger.error("→ Fix: add GROQ_API_KEY=gsk_... to .env at project root")
        return _narrative_fallback(stats, classification, weights, config)
    except Exception as e:
        logger.error(f"LLM call failed ({type(e).__name__}): {e}")
        return _narrative_fallback(stats, classification, weights, config)


# ══════════════════════════════════════════════════════════════════════════════
# Custom flowables
# ══════════════════════════════════════════════════════════════════════════════

class ColorRect(Flowable):
    def __init__(self, width, height, fill_color):
        super().__init__()
        self.width  = width
        self.height = height
        self.fill   = fill_color

    def draw(self):
        self.canv.setFillColor(self.fill)
        self.canv.rect(0, 0, self.width, self.height, stroke=0, fill=1)


class HorizontalBar(Flowable):
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


# ══════════════════════════════════════════════════════════════════════════════
# Style sheet
# ══════════════════════════════════════════════════════════════════════════════

def _make_styles():
    styles = {
        'cover_title': ParagraphStyle(
            'cover_title', fontSize=22, leading=28,
            textColor=C_WHITE, fontName='Helvetica-Bold', alignment=TA_LEFT,
        ),
        'cover_sub': ParagraphStyle(
            'cover_sub', fontSize=11, leading=16,
            textColor=C_GREEN_LIGHT, fontName='Helvetica', alignment=TA_LEFT,
        ),
        'cover_meta': ParagraphStyle(
            'cover_meta', fontSize=8, leading=13,
            textColor=C_GREEN_LIGHT, fontName='Helvetica', alignment=TA_LEFT,
        ),
        'section_head': ParagraphStyle(
            'section_head', fontSize=10, leading=14,
            textColor=C_WHITE, fontName='Helvetica-Bold',
            alignment=TA_LEFT, leftIndent=6,
        ),
        'body': ParagraphStyle(
            'body', fontSize=9, leading=14,
            textColor=C_TEXT, fontName='Helvetica',
            alignment=TA_JUSTIFY, spaceAfter=6,
        ),
        'body_bold': ParagraphStyle(
            'body_bold', fontSize=9, leading=14,
            textColor=C_TEXT, fontName='Helvetica-Bold', spaceAfter=4,
        ),
        'small': ParagraphStyle(
            'small', fontSize=7.5, leading=11,
            textColor=C_TEXT_MUTED, fontName='Helvetica', alignment=TA_LEFT,
        ),
        'small_center': ParagraphStyle(
            'small_center', fontSize=7.5, leading=11,
            textColor=C_TEXT_MUTED, fontName='Helvetica', alignment=TA_CENTER,
        ),
        'label': ParagraphStyle(
            'label', fontSize=7, leading=10,
            textColor=C_TEXT_MUTED, fontName='Helvetica-Bold',
            alignment=TA_LEFT, spaceAfter=1,
        ),
        'stat_value': ParagraphStyle(
            'stat_value', fontSize=18, leading=22,
            textColor=C_GREEN_DARK, fontName='Helvetica-Bold', alignment=TA_CENTER,
        ),
        'stat_label': ParagraphStyle(
            'stat_label', fontSize=7, leading=10,
            textColor=C_TEXT_MUTED, fontName='Helvetica', alignment=TA_CENTER,
        ),
        'caption': ParagraphStyle(
            'caption', fontSize=7.5, leading=11,
            textColor=C_TEXT_MUTED, fontName='Helvetica',
            alignment=TA_CENTER, spaceBefore=3,
        ),
        'footer': ParagraphStyle(
            'footer', fontSize=7, leading=10,
            textColor=C_TEXT_MUTED, fontName='Helvetica', alignment=TA_CENTER,
        ),
        'methodology': ParagraphStyle(
            'methodology', fontSize=8, leading=13,
            textColor=C_TEXT, fontName='Helvetica',
            alignment=TA_JUSTIFY, leftIndent=8, spaceAfter=4,
        ),
        'narrative': ParagraphStyle(
            'narrative', fontSize=9.5, leading=15,
            textColor=C_TEXT, fontName='Helvetica',
            alignment=TA_JUSTIFY, spaceAfter=8,
            firstLineIndent=12,
        ),
        'rag_source': ParagraphStyle(
            'rag_source', fontSize=7, leading=10,
            textColor=HexColor('#8a9a78'), fontName='Helvetica',
            alignment=TA_RIGHT, spaceBefore=2,
        ),
    }
    return styles


# ══════════════════════════════════════════════════════════════════════════════
# Page template
# ══════════════════════════════════════════════════════════════════════════════

def _make_page_template(county_name: str, crop: str, analysis_id: str):
    def _draw_header_footer(canvas, doc, is_cover=False):
        canvas.saveState()
        if not is_cover:
            canvas.setFillColor(C_GREEN_DARK)
            canvas.rect(0, PAGE_H - 10 * mm, PAGE_W, 10 * mm, stroke=0, fill=1)
            canvas.setFillColor(C_WHITE)
            canvas.setFont('Helvetica-Bold', 8)
            canvas.drawString(MARGIN, PAGE_H - 6.5 * mm,
                              f'{county_name} — {crop} Suitability Analysis')
            canvas.setFont('Helvetica', 7)
            canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 6.5 * mm,
                                   f'GeoFlow  ·  {analysis_id}')
            canvas.setStrokeColor(C_BORDER)
            canvas.setLineWidth(0.5)
            canvas.line(MARGIN, 12 * mm, PAGE_W - MARGIN, 12 * mm)
            canvas.setFillColor(C_TEXT_MUTED)
            canvas.setFont('Helvetica', 7)
            canvas.drawCentredString(
                PAGE_W / 2, 8 * mm,
                f'Page {doc.page}  ·  Generated {datetime.now().strftime("%d %B %Y")}  ·  For decision-support use only'
            )
        canvas.restoreState()

    return (
        lambda canvas, doc: _draw_header_footer(canvas, doc, is_cover=True),
        lambda canvas, doc: _draw_header_footer(canvas, doc, is_cover=False),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section heading
# ══════════════════════════════════════════════════════════════════════════════

def _section(title: str, styles: dict) -> list:
    bar = Table(
        [[Paragraph(title.upper(), styles['section_head'])]],
        colWidths=[CONTENT_W], rowHeights=[8 * mm],
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


# ══════════════════════════════════════════════════════════════════════════════
# Cover block
# ══════════════════════════════════════════════════════════════════════════════

def _cover_block(metadata: dict, config: dict, styles: dict) -> list:
    county  = config.get('display_name', '')
    crop    = config.get('crop', '')
    country = config.get('country', '')
    ts      = metadata.get('timestamp', datetime.now().isoformat())
    aid     = metadata.get('analysis_id', '')

    try:
        dt = datetime.fromisoformat(ts).strftime('%d %B %Y, %H:%M')
    except Exception:
        dt = ts

    cover_data = [[
        Paragraph(
            f'<font size="22"><b>{county}</b></font><br/>'
            f'<font size="13">{crop} Suitability Analysis</font>',
            ParagraphStyle('ct', fontSize=22, leading=30,
                           textColor=C_WHITE, fontName='Helvetica-Bold'),
        )
    ]]
    cover_table = Table(cover_data, colWidths=[CONTENT_W])
    cover_table.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), C_GREEN_DARK),
        ('TOPPADDING',    (0, 0), (-1, -1), 14),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
        ('LEFTPADDING',   (0, 0), (-1, -1), 12),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 12),
    ]))

    meta_data = [[
        Paragraph(f'<b>County:</b> {county}, {country}', styles['small']),
        Paragraph(f'<b>Generated:</b> {dt}', styles['small']),
        Paragraph(f'<b>ID:</b> {aid}', styles['small']),
    ]]
    meta_table = Table(meta_data, colWidths=[CONTENT_W / 3] * 3)
    meta_table.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), C_GREEN_BG),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 8),
        ('BOX',           (0, 0), (-1, -1), 0.5, C_BORDER),
        ('GRID',          (0, 0), (-1, -1), 0.3, C_BORDER),
    ]))

    return [cover_table, Spacer(1, 3 * mm), meta_table]


# ══════════════════════════════════════════════════════════════════════════════
# Score cards
# ══════════════════════════════════════════════════════════════════════════════

def _score_cards(stats: dict, styles: dict) -> Table:
    items = [
        ('Mean',    f'{stats.get("mean", 0):.1f}'),
        ('Max',     f'{stats.get("max",  0):.1f}'),
        ('Min',     f'{stats.get("min",  0):.1f}'),
        ('Std dev', f'{stats.get("std",  0):.1f}'),
    ]
    col_w = CONTENT_W / 4 - 0.5 * mm
    outer_data = [
        [Paragraph(v, styles['stat_value']) for _, v in items],
        [Paragraph(l, styles['stat_label']) for l, _ in items],
    ]
    t = Table(outer_data, colWidths=[col_w] * 4, rowHeights=[12 * mm, 5 * mm])
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


# ══════════════════════════════════════════════════════════════════════════════
# Classification table
# ══════════════════════════════════════════════════════════════════════════════

def _classification_table(classification: dict, styles: dict,
                           available_width: float = None) -> Table:
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
    data    = []

    header = [
        Paragraph('<b>Class</b>',        styles['label']),
        Paragraph('<b>% Area</b>',       styles['label']),
        Paragraph('<b>Distribution</b>', styles['label']),
    ]
    data.append(header)

    for label, key, pct in rows:
        color   = CLASS_COLORS[key]
        r, g, b = color.red, color.green, color.blue
        hex_str = '#{:02x}{:02x}{:02x}'.format(
            int(r * 255), int(g * 255), int(b * 255))
        data.append([
            Paragraph(f'<font color="{hex_str}">&#9632;</font>  {label}', styles['body']),
            Paragraph(f'<b>{pct:.1f}%</b>', styles['body']),
            HorizontalBar(BAR_W, 7, pct, color),
        ])

    col_widths = [total_w - BAR_W - 20 * mm, 20 * mm, BAR_W]
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('BACKGROUND',     (0, 0), (-1, 0),  C_GREEN_BG),
        ('BOX',            (0, 0), (-1, -1), 0.5, C_BORDER),
        ('LINEBELOW',      (0, 0), (-1, 0),  0.5, C_BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [C_WHITE, HexColor('#fafff7')]),
        ('TOPPADDING',     (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING',  (0, 0), (-1, -1), 4),
        ('LEFTPADDING',    (0, 0), (-1, -1), 6),
        ('VALIGN',         (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    return t


# ══════════════════════════════════════════════════════════════════════════════
# Weights table
# ══════════════════════════════════════════════════════════════════════════════

def _weights_table(weights: dict, config: dict, styles: dict) -> Table:
    criteria_info = config.get('criteria_info', {})
    header = [
        Paragraph('<b>Criterion</b>',     styles['label']),
        Paragraph('<b>Weight</b>',         styles['label']),
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
        ('BACKGROUND',     (0, 0), (-1, 0),  C_GREEN_BG),
        ('BOX',            (0, 0), (-1, -1), 0.5, C_BORDER),
        ('LINEBELOW',      (0, 0), (-1, 0),  0.5, C_BORDER),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [C_WHITE, HexColor('#fafff7')]),
        ('TOPPADDING',     (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING',  (0, 0), (-1, -1), 4),
        ('LEFTPADDING',    (0, 0), (-1, -1), 6),
        ('VALIGN',         (0, 0), (-1, -1), 'TOP'),
        ('GRID',           (0, 0), (-1, -1), 0.3, C_BORDER),
    ]))
    return t


# ══════════════════════════════════════════════════════════════════════════════
# Image helper
# ══════════════════════════════════════════════════════════════════════════════

def _img(path, max_width: float, max_height: float) -> Optional[Image]:
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


# ══════════════════════════════════════════════════════════════════════════════
# Main builder
# ══════════════════════════════════════════════════════════════════════════════

def build_report(
    analysis_id: str,
    metadata: dict,
    rendered: dict,
    config: dict,
    paths: dict,
    depth: str = 'full',
) -> Path:
    output_path = paths['api_results_dir'] / f'report_{analysis_id}.pdf'
    styles      = _make_styles()

    county_name = config.get('display_name', '')
    crop        = config.get('crop', '')
    stats       = metadata.get('statistics', {})
    classif     = metadata.get('classification', {})
    weights     = metadata.get('weights', config.get('weights', {}))

    on_first, on_later = _make_page_template(county_name, crop, analysis_id)

    doc = SimpleDocTemplate(
        str(output_path), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN + 10 * mm,
        bottomMargin=MARGIN + 6 * mm,
        title=f'{county_name} {crop} Suitability Report',
        author='GeoFlow Analysis Engine',
    )

    story = []

    # ── PAGE 1: Cover + map + stats ────────────────────────────────────────────
    story += _cover_block(metadata, config, styles)
    story.append(Spacer(1, 4 * mm))

    story += _section('Suitability Map', styles)
    suit_map = _img(rendered.get('suitability_map'), CONTENT_W, 110 * mm)
    if suit_map:
        story.append(suit_map)
        story.append(Paragraph(
            f'Figure 1. {crop} suitability across {county_name}. '
            f'Four-class weighted overlay using {len(weights)} biophysical criteria. '
            f'Dashed line = county boundary.',
            styles['caption'],
        ))
    else:
        story.append(Paragraph('Map image not available.', styles['small']))

    story.append(Spacer(1, 4 * mm))
    story += _section('Results Summary', styles)
    story.append(_score_cards(stats, styles))
    story.append(Spacer(1, 3 * mm))

    chart_w   = CONTENT_W * 0.46
    classif_w = CONTENT_W * 0.54
    suit_chart = _img(rendered.get('classification_chart'), chart_w, 50 * mm)
    classif_t  = _classification_table(classif, styles, available_width=classif_w - 4 * mm)

    if suit_chart:
        side_table = Table([[suit_chart, classif_t]], colWidths=[chart_w, classif_w])
        side_table.setStyle(TableStyle([
            ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING',  (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ]))
        story.append(side_table)
    else:
        story.append(_classification_table(classif, styles))

    # ── Narrative ──────────────────────────────────────────────────────────────
    story += _section('Interpretation', styles)

    # Log clearly whether LLM or fallback will be used
    llm_available = any(
        (key_var and os.environ.get(key_var)) or key_var is None
        for _, (_, key_var) in _PROVIDERS.items()
    )
    if not llm_available:
        logger.warning(
            "No LLM keys found — narrative will use fallback template. "
            "Add GROQ_API_KEY to .env to enable AI narratives."
        )

    narrative = generate_narrative(
        stats, classif, weights, config,
        rag_context=metadata.get("rag_context", ""),
    )

    for para in narrative.split('\n\n'):
        para = para.strip()
        if para:
            # Flag fallback paragraphs visually in the PDF
            if para.startswith('[NOTE:'):
                story.append(Paragraph(para, styles['small']))
            else:
                story.append(Paragraph(para, styles['narrative']))

    # RAG attribution footnote
    if _RAG_AVAILABLE:
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(
            'Interpretation informed by retrieved agronomic knowledge base.',
            styles['rag_source'],
        ))

    story.append(Spacer(1, 4 * mm))
    story += _section('Criterion Weights Used', styles)
    story.append(_weights_table(weights, config, styles))

    weight_chart = _img(rendered.get('weight_chart'), CONTENT_W * 0.6, 55 * mm)
    if weight_chart:
        story.append(Spacer(1, 3 * mm))
        story.append(weight_chart)
        story.append(Paragraph(
            'Figure 2. Relative importance of each criterion in the weighted overlay.',
            styles['caption'],
        ))

    # ── FULL DEPTH: Pages 3–4 ──────────────────────────────────────────────────
    if depth == 'full':
        story.append(PageBreak())
        story += _section('Individual Criterion Layers', styles)
        story.append(Paragraph(
            'Each layer shows the normalised suitability score (0–100) for that '
            'criterion alone, before weighting. This helps identify which specific '
            'factor limits or enables suitability in each part of the county.',
            styles['body'],
        ))
        story.append(Spacer(1, 3 * mm))
        grid = _img(rendered.get('criteria_grid'), CONTENT_W, 160 * mm)
        if grid:
            story.append(grid)
            story.append(Paragraph(
                'Figure 3. Normalised individual criterion scores (0–100). '
                'Colormaps are independent per layer; do not compare shades across panels.',
                styles['caption'],
            ))

        story.append(PageBreak())
        story += _section('Methodology', styles)
        norm_config = config.get('normalization', {})
        story.append(Paragraph(
            'This analysis uses a Multi-Criteria Decision Analysis (MCDA) weighted overlay '
            'approach. Each biophysical layer is normalised to a 0–100 suitability score '
            'using a fuzzy membership function, then combined using analyst-defined weights '
            'that sum to 1.0. The final score represents a weighted average of criterion '
            'suitability across the landscape.',
            styles['body'],
        ))
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph('Normalisation functions applied:', styles['body_bold']))
        for name, norm in norm_config.items():
            fn_type   = norm.get('type', '')
            params    = norm.get('params', {})
            desc      = norm.get('description', '')
            param_str = '  ·  '.join(f'{k} = {v}' for k, v in params.items())
            story.append(Paragraph(
                f'<b>{name.capitalize()}</b> ({fn_type}): {desc}<br/>'
                f'<font color="#7a8f68">{param_str}</font>',
                styles['methodology'],
            ))

        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph('Classification thresholds:', styles['body_bold']))
        for label, rng in [
            ('Highly suitable', '70–100'), ('Moderately suitable', '50–70'),
            ('Marginally suitable', '30–50'), ('Not suitable', '0–30'),
        ]:
            story.append(Paragraph(f'<b>{label}:</b>  score {rng}', styles['methodology']))

        story.append(Spacer(1, 4 * mm))
        story += _section('Data Sources & Limitations', styles)
        story.append(Paragraph(
            'Input layers were sourced from publicly available global datasets '
            '(SRTM elevation, CHIRPS rainfall, WorldClim temperature, SoilGrids clay content). '
            'Slope was derived from the DEM. Protected area constraints were applied where '
            'available. All layers were resampled to a common pixel grid before analysis.',
            styles['body'],
        ))
        story.append(Paragraph(
            'Limitations: spatial resolution of source data (250 m – 1 km) may not capture '
            'fine-scale variation within sub-county units. Temporal averages mask seasonal '
            'variability. Results represent biophysical potential only and do not account for '
            'socioeconomic factors, market access, or current land use. Field validation is '
            'recommended before operational decisions are made.',
            styles['body'],
        ))
        story.append(Paragraph(
            'This report is generated for decision-support purposes only and should not be '
            'used as a substitute for professional agronomic assessment.',
            styles['small'],
        ))

    doc.build(story, onFirstPage=on_first, onLaterPages=on_later)
    logger.info(f'Report saved: {output_path.name}  ({depth})')
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# Standalone test
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    sys.path.append(str(Path(__file__).parent))

    print("=== report_writer diagnostic ===\n")
    print(f"GROQ_API_KEY:   {'SET ✅' if os.environ.get('GROQ_API_KEY') else 'MISSING ❌'}")
    print(f"LLM_PROVIDER:   {os.environ.get('LLM_PROVIDER', '(auto-detect)')}")
    print(f"RAG available:  {_RAG_AVAILABLE}")
    print()

    # Try a live LLM call
    print("Testing LLM call...")
    try:
        result = _call_llm("Say 'LLM OK' and nothing else.")
        print(f"LLM response: {result}")
    except Exception as e:
        print(f"LLM FAILED: {e}")

    print()

    dummy_metadata = {
        'analysis_id': 'test_20250101_120000',
        'county':      'kitui',
        'timestamp':   datetime.now().isoformat(),
        'statistics':  {'mean': 54.2, 'max': 91.3, 'min': 3.1, 'std': 18.7, 'median': 56.1},
        'classification': {
            'highly_suitable_pct':     22.4,
            'moderately_suitable_pct': 35.1,
            'marginally_suitable_pct': 18.9,
            'not_suitable_pct':         8.3,
            'excluded_pct':            15.3,
        },
        'weights': {'rainfall': 0.30, 'elevation': 0.15, 'temperature': 0.20, 'soil': 0.20, 'slope': 0.15},
    }
    dummy_config = {
        'county': 'kitui', 'display_name': 'Kitui County',
        'country': 'Kenya', 'crop': 'Cotton',
        'weights': dummy_metadata['weights'],
        'criteria_info': {
            'rainfall':    {'description': 'Annual rainfall mm/yr',    'optimal_range': '600–900 mm'},
            'elevation':   {'description': 'Elevation above sea level', 'optimal_range': '400–1000 m'},
            'temperature': {'description': 'Mean annual temperature',   'optimal_range': '22–32°C'},
            'soil':        {'description': 'Soil clay content g/kg',    'optimal_range': '200–400 g/kg'},
            'slope':       {'description': 'Terrain slope degrees',     'optimal_range': '0–5° (max 15°)'},
        },
        'normalization': {
            'rainfall':    {'type': 'trapezoidal',      'params': {'a':400,'b':600,'c':900,'d':1200},  'description': 'Semi-arid 600-900mm optimal'},
            'elevation':   {'type': 'trapezoidal',      'params': {'a':200,'b':400,'c':1000,'d':1500}, 'description': 'Lowland cotton 400-1000m optimal'},
            'temperature': {'type': 'gaussian',         'params': {'optimal':27,'spread':5},            'description': 'Warmer optimum for ASAL cotton'},
            'soil':        {'type': 'trapezoidal',      'params': {'a':100,'b':200,'c':400,'d':550},   'description': 'Moderate clay 200-400 optimal'},
            'slope':       {'type': 'linear_descending','params': {'min_val':0,'max_val':15},           'description': 'Flat land best, zero above 15°'},
        },
    }

    out_dir = Path('/tmp/geoflow_test')
    out_dir.mkdir(exist_ok=True)

    # Build RAG store if docs present
    build_rag_store()

    pdf = build_report(
        analysis_id='test_20250101_120000',
        metadata=dummy_metadata,
        rendered={},
        config=dummy_config,
        paths={'api_results_dir': out_dir, 'boundary': Path('/nonexistent')},
        depth='full',
    )
    print(f'\nReport: {pdf}')