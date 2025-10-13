import streamlit as st
import re
import base64
import streamlit.components.v1 as components


from auth import sleep_step

from datetime import datetime
import html, random

def mock_fetch_intent_result(intent: str) -> dict:
    """Mock of your API result. Replace with the real call later."""
    long_texts = {
        "Business Description": (
            "The company operates a diversified platform with recurring revenue streams "
            "across software subscriptions and transaction processing. Go-to-market is hybrid "
            "(direct + partners) with concentration in the mid-market. Unit economics show "
            "steady CAC payback under 12 months with gross retention >90% and NRR ~112%. "
            "Key dependencies include cloud infra providers and a two-sided network of ISVs and channel partners. "
            "Regulatory exposure is limited but expanding with payments attach. "
            "Growth is expected to normalize as larger cohorts mature, with incremental margin from automation."
        ),
        "Recent Developments": (
            "Management closed two tuck-ins in Q2 focused on workflow automation; integrations are on-track. "
            "Pricing was re-aligned for tiered value, with minimal logo churn. "
            "A targeted restructuring reduced OpEx by ~6% while preserving roadmap capacity. "
            "Debt refi extended maturities to 2029 at a modest spread increase; covenant headroom remains ample. "
            "Customer health mixed: usage stabilizing in SMB while enterprise pilots expand."
        ),
        "ABL": (
            "Borrowing base primarily AR (Net 85) with immaterial inventory. "
            "Advance rates align with policy (85% AR, 20% inventory cap). "
            "Dilution/offsets trend at 2.1%–2.6%; top-10 obligors <30% of AR. "
            "Covenants include springing FCCR and minimum liquidity. "
            "Field exam flagged minor documentation gaps; remediation underway. "
            "No in-eligibles from cross-aging; concentrations monitored monthly."
        ),
    }
    return {
        "intent": intent,
        "llm_response": long_texts.get(intent, "Generated text... " * 20),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

def make_snippet(text: str, limit: int = 280) -> str:
    text = (text or "").strip()
    if len(text) <= limit: 
        return text
    cut = text.rfind(" ", 0, limit)
    if cut == -1: 
        cut = limit
    return text[:cut] + "…"


DOC_TYPES = ["10K", "10Q", "Earnings", "Underwriting Memo", "Inventory Appraisal", "Field Exam"]

TRIGGER_START_AFTER = 2   # start Trigger Evaluation once this many docs are ingested

# --- Bulk ingest failure config ---
BULK_FAIL_THRESHOLD = 2     # trigger the failure path when total docs > this
BULK_FAIL_DOC_INDEX = 1     # 0-based index of the doc that fails once (2 => 3rd doc)




# --- Per-stage timings (seconds) ---
SIM = {
    "dp_progress": 1.00,   # each of the first 4 nodes: progress animation
    "dp_success":  0.35,   # short settle after success
    "fo_progress": 0.90,   # per-doc ingest "Ingesting…"
    "fo_success":  0.45,   # per-doc "Ready" settle
    "tr_progress": 1.00,   # Trigger Evaluation progress
    "tr_success":  0.35,   # Trigger Evaluation success settle
}

SIM.update({
    "tr_start":  0.80,   # when TE first flips to progress
    "tr_tick":   0.50,   # each payload sent (counter increments)
    "tr_finish": 0.60,   # settle after last payload, before success
})

SIM.update({
    "ai_progress": 0.80,   # highlight current AI stage
    "ai_advance":  0.60,   # move payloads to next stage
    "ai_settle":   0.35,   # small settle after each hop
})



# Optional global multiplier for quick tuning (e.g., 1.0 normal, 1.5 slower, 2.0 slowest)
SPEED_FACTOR = 1.75

# Use existing sleep_step for smooth repaint; fall back if missing
def _sleep_smooth(seconds: float):
    try:
        sleep_step(seconds)   # your existing function
    except NameError:
        import time
        # fallback: chunked sleep so Streamlit can repaint
        steps = max(1, int(seconds * 5))
        for _ in range(steps):
            time.sleep(seconds / steps)

def wait(key: str):
    dur = SIM[key] * SPEED_FACTOR
    _sleep_smooth(dur)

def _fanout_height(n_docs: int) -> int:
    # ~110px per card row + chrome
    rows = max(1, (n_docs + 2) // 3)  # 3 cards per row typical
    return 120 + rows * 120

import random
random.seed(7)  # deterministic demo; change/remove for more variety

# Time to traverse each AI stage j -> j+1 (seconds, pre-jitter)
# indexes: 0:Receive→Prompt, 1:Prompt→Download, 2:Download→Context,
#          3:Context→Invocation, 4:Invocation→Output
AI_STAGE_BASE = [0.7, 0.6, 0.6, 0.8, 1.1]

def _stage_duration(stage: int) -> float:
    """Randomized dwell time for a payload at a given stage, scaled by SPEED_FACTOR."""
    base = AI_STAGE_BASE[stage]
    jitter = random.uniform(0.75, 1.35)
    return base * jitter * SPEED_FACTOR


 
DATE_RE = re.compile(r"^(?:\d{4}|Q[1-4]\d{4})$")  # 2024 or Q22024

def is_valid_business_date(s: str) -> bool:
    if not s:
        return False
    return bool(DATE_RE.match(s.strip()))


# ===============================
# THEME / COLORS (GS palette)
# ===============================

st.set_page_config(page_title="Memo Generation Demo", layout="wide")

PRIMARY        = "#7297C5"  
PRIMARY_DARK   = "#4F79B1"
PRIMARY_LIGHT  = "#AFC3E1"
BG_TOP         = "#F7FAFF"
BG_MID         = "#EFF4FB"
BG_BOTTOM      = "#E6EFFA"
TEXT_DARK      = "#16324A"

# ---------- MISSING BASICS (defined only if absent) ----------
import time as _time

# Colors
if "PRIMARY" not in globals(): PRIMARY = "#7297C5"
if "TEXT_DARK" not in globals(): TEXT_DARK = "#16324A"
if "PENDING"   not in globals(): PENDING   = "#98A6B3"
if "PROGRESS"  not in globals(): PROGRESS  = "#F4C542"
if "SUCCESS"   not in globals(): SUCCESS   = "#34C759"  

# Document Processing nodes
if "DOC_NODES" not in globals():
    DOC_NODES = [
        "Document Upload",
        "S3 Upload",
        "Section Coverage Analysis",
        "Proxy Document Retriever",
        "Async DB Ingestion",
        "Trigger Evaluation",
    ]

# Credit AI nodes (stacked pipeline under Document Processing)
AI_NODES = [
    "Receive Generation Payloads",
    "Prompt Manager",
    "Download Documents",
    "Context Assembly / Upload",
    "Credit AI Invocation",
    "Output Delivery",
]

# We have 3 sections now
PAYLOAD_SECTION_NAMES = ["Business Description", "Recent Developments", "ABL"]
TOTAL_INTENTS = len(PAYLOAD_SECTION_NAMES)  # = 3

# Smooth UI repaint sleep
if "sleep_step" not in globals():
    def sleep_step(seconds: float):
        seconds *= SIM_SPEED            # << scale all waits here
        steps = int(seconds * 5)
        for _ in range(steps):
            _time.sleep(seconds / steps)

def _ai_counts(payloads_idx, n_nodes):
    counts = [0]*n_nodes
    for idx in payloads_idx:
        # clamp in case something already finished
        j = min(idx, n_nodes-1)
        counts[j] += 1
    return counts

def _ai_counts(payloads_idx, n_nodes):
    counts = [0]*n_nodes
    for idx in payloads_idx:
        counts[min(idx, n_nodes-1)] += 1
    return counts

def _paint_ai_lane(ai_lane_area, payloads_idx):
    last = len(AI_NODES) - 1
    counts = _ai_counts(payloads_idx, len(AI_NODES))

    states, labels = [], []
    for j, name in enumerate(AI_NODES):
        at_j   = counts[j]                         # how many are exactly at node j
        after  = sum(1 for x in payloads_idx if x > j)

        if j == last:
            # last node: success when everyone has arrived (>= last),
            # progress while some are arriving.
            if all(x >= last for x in payloads_idx):
                state = "success"
            elif at_j > 0:
                state = "progress"
            else:
                state = "pending"
        else:
            # intermediate nodes: success when everyone has moved beyond,
            # progress if anyone is currently here.
            if after == len(payloads_idx):
                state = "success"
            elif at_j > 0:
                state = "progress"
            else:
                state = "pending"

        states.append(state)
        labels.append(name)  # no counters on the pills

    html = f'<div class="board">{lane_html("🤖 Credit AI", labels, states)}</div>'
    ai_lane_area.markdown(html, unsafe_allow_html=True)


def _render_occupancy_row(payloads_idx, total_payloads):
    counts = _ai_counts(payloads_idx, len(AI_NODES))
    cells = []
    for c in counts:
        dots = ''.join(f'<span class="occ-dot {"on" if k < c else ""}"></span>' for k in range(total_payloads))
        cells.append(f'<div class="occ-cell">{dots}</div>')
    return f'<div class="occ-strip">{"".join(cells)}</div>'

def _render_payload_cards(names, idxs, results_map):
    last = len(AI_NODES) - 1
    cards = []
    for name, idx in zip(names, idxs):
        segs = ''.join(f'<span class="seg {"on" if s <= idx else ""}"></span>' for s in range(len(AI_NODES)))
        at_name = AI_NODES[min(idx, last)]
        done = idx >= last
        pill = "success" if done else "progress"
        if done and name in results_map:
            res = results_map[name]
            ts = html.escape(res.get("timestamp",""))
            full = html.escape(res.get("llm_response",""))
            snippet = html.escape(make_snippet(res.get("llm_response","")))
            body = (
                f'<div class="doc-meta">Delivered • {ts}</div>'
                f'<div class="doc-meta">{snippet}</div>'
                f'<details class="card-details"><summary class="view-link">View full response</summary>'
                f'<div class="fulltext">{full}</div></details>'
            )
        else:
            body = f'<div class="doc-meta">At: {html.escape(at_name)}</div>'
        cards.append(
            f'<div class="doc-chip">'
            f'<h5>{html.escape(name)}</h5>'
            f'{body}'
            f'<div class="segbar">{segs}</div>'
            f'<span class="status-pill {pill}">{"Done" if done else "Processing"}</span>'
            f'</div>'
        )
    return (
        '<div class="fanout-card">'
        '<div class="fanout-title">Per-intent payloads (parallel)</div>'
        f'<div class="doc-grid">{"".join(cards)}</div>'
        '</div>'
    )



# Minimal lane CSS (safe to include even if you already have styles)
LANE_BASE_CSS = f"""
<style>
.board {{
  background: rgba(255,255,255,0.98);
  border: 1px solid rgba(22,50,74,0.08);
  border-radius: 16px;
  box-shadow: 0 8px 24px rgba(22,50,74,0.08);
  padding: 14px 16px;
}}
.group-title {{ font-weight: 800; color: {PRIMARY}; margin: 4px 0 8px 4px; letter-spacing: .2px; }}
.lane {{
  display: flex; align-items: center; gap: 14px; flex-wrap: nowrap;
  overflow-x: auto; padding: 10px 8px 14px 8px; border-radius: 12px;
  background: #FAFDFF; border: 1px dashed rgba(79,121,177,0.18); min-width: 960px;
}}
.node-pill {{
  flex-shrink: 0; min-width: 160px; text-align: center; white-space: nowrap;
  padding: 10px 14px; border-radius: 999px; font-weight: 700; color: #173044;
  background: #EEF3F7; border: 1px solid rgba(25,53,74,0.08);
  transition: background-color .6s ease, box-shadow .6s ease, border-color .6s ease, color .6s ease;
}}
.arrow {{ font-size: 22px; color: rgba(60,84,96,0.65); user-select: none; }}
.node-pill.pending  {{ background:#EEF3F7; color:#41515C; }}
.node-pill.progress {{ background:{PROGRESS}; color:#132C3C; }}
.node-pill.success  {{ background:{SUCCESS}; color:#06220E; }}
</style>
"""
st.markdown("""
<style>
/* Wrap each pill so we can anchor a badge in its corner */
.node-wrap{ position:relative; display:inline-block; }
.node-pill{ position:relative; z-index:1; }

/* Live retry badge (red) */
.retry-badge{
  position:absolute; top:-8px; right:-8px;
  background:#E74C3C; color:#fff; font-weight:800;
  border-radius:10px; padding:2px 6px; font-size:11px; line-height:1;
  box-shadow:0 4px 10px rgba(231,76,60,0.28);
}

/* Scar (subtle) that stays after recovery */
.retry-scar{
  position:absolute; top:-6px; right:-6px;
  width:10px; height:10px; border-radius:50%;
  background:#A8B6C8;
  box-shadow:0 0 0 2px rgba(168,182,200,0.25);
}

/* Event chip row under the lane */
.event-row{ margin:6px 2px 0; display:flex; gap:8px; flex-wrap:wrap; }
.event-chip{
  background:#F3F7FD; color:#435768; border:1px solid rgba(22,50,74,0.10);
  padding:4px 8px; border-radius:999px; font-size:12px; font-weight:700;
}
.event-chip .red{ color:#E74C3C; }
.event-chip .green{ color:#2E7D32; }
</style>
""", unsafe_allow_html=True)

st.markdown(LANE_BASE_CSS, unsafe_allow_html=True)

def lane_html(title, nodes, states, retry_badges=None, events_html=""):
    # retry_badges: dict[int -> {"type": "live"|"scar", "label": "↶", "title": "..."}]
    arrow_sep = '<span class="arrow">→</span>'
    pills = []
    for i, (label, s) in enumerate(zip(nodes, states)):
        pill = f'<div class="node-pill {s}">{html.escape(label)}</div>'
        badge = ""
        if retry_badges and i in retry_badges: 
            b = retry_badges[i]
            if b.get("type") == "live":
                badge = f'<span class="retry-badge" title="{html.escape(b.get("title","Retry"))}">{html.escape(b.get("label","↶"))}</span>'
            elif b.get("type") == "scar":
                badge = f'<span class="retry-scar" title="{html.escape(b.get("title","1 retry"))}"></span>'
        pills.append(f'<span class="node-wrap">{pill}{badge}</span>')
    joined = (' ' + arrow_sep + ' ').join(pills)

    content = (
        '<div class="group-title">' + html.escape(title) + '</div>'
        '<div class="lane">' + joined + '</div>'
    )
    if events_html:
        content += f'<div class="event-row">{events_html}</div>'
    return content




GLOBAL_CSS = f"""
<style>
/* Force light background even if Streamlit is in dark mode */
[data-testid="stAppViewContainer"] {{
  background: linear-gradient(135deg, {BG_TOP} 0%, {BG_MID} 45%, {BG_BOTTOM} 100%);
}}
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 0 !important; padding-bottom: 0 !important; }}

/* Hero */
.hero-wrap {{
  padding: 10vh 0 2vh;   /* not full-height → leaves room for buttons */
  display: block;
}}
.hero {{
  width: min(920px, 92vw);
  margin: 0 auto;
  background: rgba(255,255,255,0.98);
  border: 1px solid rgba(22,50,74,0.08);
  border-radius: 18px;
  box-shadow: 0 16px 40px rgba(22,50,74,0.10);
  padding: 36px 36px 24px 36px;
  text-align: center;
}}
.hero h1 {{ margin: 0 0 10px; color: {TEXT_DARK}; font-weight: 800; }}
.hero p  {{ margin: 0; color: #435768; }}

/* Button row right under the hero */
.btn-wrap {{
  width: min(920px, 92vw);
  margin: 14px auto 0 auto;
  display: flex; gap: 14px; justify-content: center;
}}

.primary-btn button {{
  background: linear-gradient(135deg, {PRIMARY} 0%, {PRIMARY_DARK} 100%) !important;
  color: #fff !important; border: 0 !important;
  padding: 0.9rem 1.4rem !important;
  border-radius: 12px !important; font-weight: 800 !important;
  box-shadow: 0 10px 22px rgba(79,121,177,0.28);
}}
.primary-btn button:hover {{
  transform: translateY(-2px) scale(1.02);
  box-shadow: 0 14px 26px rgba(79,121,177,0.36);
}}
.secondary-btn button {{
  background: transparent !important; color: {PRIMARY_DARK} !important;
  border: 2px solid {PRIMARY_DARK} !important;
  padding: 0.85rem 1.25rem !important; border-radius: 12px !important; font-weight: 800 !important;
}}
.secondary-btn button:hover {{
  transform: translateY(-2px) scale(1.02);
  background: {PRIMARY_LIGHT}22 !important;
  border-color: {PRIMARY} !important;
}}
</style>
"""

PRIMARY        = "#7297C5"
PRIMARY_DARK   = "#4F79B1"
PRIMARY_LIGHT  = "#AFC3E1"
TEXT_DARK      = "#16324A"

UPLOAD_CSS = f"""
<style>
/* Force light app background even in dark mode */
[data-testid="stAppViewContainer"] {{
  background: linear-gradient(135deg, #F7FAFF 0%, #EFF4FB 45%, #E6EFFA 100%);
}}
[data-testid="stHeader"] {{ background: transparent; }}

/* -------- Inputs: text + select -------- */
[data-testid="stTextInput"] input,
[data-testid="stSelectbox"] [role="combobox"],
[data-testid="stDateInput"] input {{
  background: #FFFFFF !important;
  color: {TEXT_DARK} !important;
  border: 1.5px solid {PRIMARY_LIGHT} !important;
  border-radius: 12px !important;
  padding: 10px 12px !important;
  box-shadow: 0 1px 2px rgba(22,50,74,0.06) inset;
}}
/* Focus ring */
/*[data-testid="stTextInput"] input:focus,*/
/*[data-testid="stSelectbox"] [role="combobox"]:focus,*/
/*[data-testid="stDateInput"] input:focus {{*/
  /*outline: none !important;*/
  /*border-color: {PRIMARY} !important;*/
  /*box-shadow: 0 0 0 3px {PRIMARY}22 !important;*/
}}
/* Labels */
[data-testid="stWidgetLabel"] > p {{
  color: {TEXT_DARK} !important;
  font-weight: 700 !important;
}}

/* -------- File uploader -------- */
[data-testid="stFileUploaderDropzone"] {{
  background: #FFFFFF !important;
  border: 2px dashed {PRIMARY_LIGHT} !important;
  color: {TEXT_DARK} !important;
  border-radius: 14px !important;
}}
[data-testid="stFileUploaderDropzone"]:hover {{
  border-color: {PRIMARY} !important;
  background: {PRIMARY_LIGHT}11 !important;
}}
[data-testid="stFileUploaderFile"] p {{
  color: {TEXT_DARK} !important;
}}

/* -------- Expander per-doc card -------- */
[data-testid="stExpander"] details {{
  background: #FFFFFF !important;
  border: 1px solid rgba(22,50,74,0.10) !important;
  border-radius: 14px !important;
  box-shadow: 0 6px 16px rgba(22,50,74,0.06);
}}
[data-testid="stExpander"] summary {{
  color: {TEXT_DARK} !important;
  font-weight: 700 !important;
}}
[data-testid="stExpander"] svg {{ color: {PRIMARY_DARK} !important; }}

/* -------- Buttons (kills Streamlit red) -------- */
.stButton > button {{
  background: linear-gradient(135deg, {PRIMARY} 0%, {PRIMARY_DARK} 100%) !important;
  color: #fff !important;
  border: 0 !important;
  border-radius: 12px !important;
  padding: 0.9rem 1.2rem !important;
  font-weight: 800 !important;
  box-shadow: 0 8px 18px rgba(79,121,177,0.28);
}}
.stButton > button:hover {{
  transform: translateY(-1px);
  box-shadow: 0 12px 22px rgba(79,121,177,0.36);
}}

/* -------- Optional: form card wrapper -------- */
.form-card {{
  background: rgba(255,255,255,0.98);
  border: 1px solid rgba(22,50,74,0.08);
  border-radius: 18px;
  box-shadow: 0 16px 40px rgba(22,50,74,0.10);
  padding: 22px 20px;
}}
</style>
"""

FIX_SELECTBOX_CSS = f"""
<style>
/* --- Text inputs only (leave selectbox out of this) --- */
[data-testid="stTextInput"] input,
[data-testid="stDateInput"] input {{
  background: #FFFFFF !important;
  color: {TEXT_DARK} !important;
  border: 1.5px solid {PRIMARY_LIGHT} !important;
  border-radius: 12px !important;
  padding: 10px 12px !important;
  box-shadow: 0 1px 2px rgba(22,50,74,0.06) inset;
}}
[data-testid="stTextInput"] input:focus,
[data-testid="stDateInput"] input:focus {{
  outline: none !important;
  border-color: {PRIMARY} !important;
  box-shadow: 0 0 0 3px {PRIMARY}22 !important;
}}

/* --- Selectbox (wrapper) --- */
[data-testid="stSelectbox"] [role="combobox"] {{
  background: #FFFFFF !important;
  color: {TEXT_DARK} !important;
  border: 1.5px solid {PRIMARY_LIGHT} !important;
  border-radius: 12px !important;
  padding: 10px 12px !important;
  box-shadow: 0 1px 2px rgba(22,50,74,0.06) inset;
}}
/* Focus ring when anything inside is focused */
[data-testid="stSelectbox"] [role="combobox"]:focus-within {{
  outline: none !important;
  border-color: {PRIMARY} !important;
  box-shadow: 0 0 0 3px {PRIMARY}22 !important;
}}

/* --- Kill the ghost inner input inside the select --- */
[data-testid="stSelectbox"] [role="combobox"] input {{
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
  outline: none !important;
  padding: 0 !important;
  margin: 0 !important;
  height: 24px !important;          /* keeps the control height tidy */
  color: inherit !important;
}}
/* Ensure internal chips/labels don't add a background */
[data-testid="stSelectbox"] [role="combobox"] > div {{
  background: transparent !important;
}}
/* Chevron color */
[data-baseweb="select"] svg {{
  color: {PRIMARY_DARK} !important;
}}

/* Optional: dropdown menu styling for better contrast */
[data-baseweb="menu"] {{
  background: #FFFFFF !important;
  color: {TEXT_DARK} !important;
  border: 1px solid {PRIMARY_LIGHT} !important;
  box-shadow: 0 10px 24px rgba(22,50,74,0.12) !important;
  border-radius: 12px !important;
}}
</style>
"""
FIX_GHOST_SELECT_INPUT = f"""
<style>
/* Keep the select wrapper styled & with focus ring */
[data-testid="stSelectbox"] [role="combobox"] {{
  position: relative;
  overflow: hidden;                 /* hide any inner pill corners */
  background: #FFFFFF !important;
  color: {TEXT_DARK} !important;
  border: 1.5px solid {PRIMARY_LIGHT} !important;
  border-radius: 12px !important;
  padding: 10px 12px !important;
  box-shadow: 0 1px 2px rgba(22,50,74,0.06) inset;
}}
[data-testid="stSelectbox"] [role="combobox"]:focus-within {{
  outline: none !important;
  border-color: {PRIMARY} !important;
  box-shadow: 0 0 0 3px {PRIMARY}22 !important;
}}

/* Hide the internal search <input> Safari/BaseWeb renders */
[data-testid="stSelectbox"] [role="combobox"] input,
div[data-baseweb="select"] input {{
  opacity: 0 !important;            /* invisible but still present */
  width: 0 !important;
  min-width: 0 !important;
  height: 0 !important;
  padding: 0 !important;
  margin: 0 !important;
  border: 0 !important;
  box-shadow: none !important;
  background: transparent !important;
  outline: none !important;
  caret-color: transparent !important;
  pointer-events: none !important;  /* prevents cursor showing */
}}
/* Chevron color */
[data-baseweb="select"] svg {{ color: {PRIMARY_DARK} !important; }}
</style>
"""
FANOUT_CSS = f"""
<style>
/* Fan-out card */
.fanout-card {{
  background: rgba(255,255,255,0.98);
  border: 1px solid rgba(22,50,74,0.08);
  border-radius: 16px;
  box-shadow: 0 10px 26px rgba(22,50,74,0.08);
  padding: 14px 16px;
  margin-top: 10px;
}}
.fanout-title {{
  font-weight: 800; color: {PRIMARY}; margin: 0 0 8px 2px; letter-spacing: .2px;
}}
.doc-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}}
.doc-chip {{
  background: #FFFFFF;
  border: 1px solid rgba(22,50,74,0.10);
  border-radius: 14px;
  box-shadow: 0 6px 16px rgba(22,50,74,0.06);
  padding: 10px 12px;
}}
.doc-chip h5 {{
  margin: 0 0 6px 0; font-size: 0.98rem; color: {TEXT_DARK}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.doc-meta {{ color:#5d6c79; font-size: 12px; margin-top:2px; }}
.status-pill {{
  display: inline-block; padding: 3px 8px; border-radius: 999px; font-weight: 700; font-size: 12px; margin-top: 8px;
}}
.status-pill.pending  {{ background:#EEF3F7; color:#41515C; }}
.status-pill.progress {{ background:{PROGRESS}; color:#132C3C; }}
.status-pill.success  {{ background:{SUCCESS}; color:#06220E; }}
</style>
"""
OCCUPANCY_CSS = f"""
<style>
/* Row of occupancy dots (one cell per AI node) */
.occ-strip {{ 
  display: grid; grid-template-columns: repeat({len(AI_NODES)}, 1fr);
  gap: 10px; margin: 6px 2px 8px 2px;
}}
.occ-cell {{
  display: flex; justify-content: center; align-items: center; gap: 6px;
}}
.occ-dot {{
  width: 8px; height: 8px; border-radius: 50%;
  background: #dbe6f4;
}}
.occ-dot.on {{
  background: {PRIMARY_DARK}; box-shadow: 0 0 0 3px rgba(79,121,177,0.18);
}}

/* Global in-flight meter (right-aligned) */
.global-meter {{
  display: flex; align-items: center; gap: 8px; justify-content: flex-end; margin: 2px 4px 8px;
  color: #5d6c79; font-size: 13px;
}}
.gdot {{ width: 10px; height: 10px; border-radius: 50%; background: #dbe6f4; }}
.gdot.on {{ background: {PRIMARY_DARK}; }}

/* Mini 6-segment progress bar in each payload card */
.segbar {{
  display: grid; grid-template-columns: repeat({len(AI_NODES)},1fr); gap: 3px; margin-top: 8px;
}}
.seg {{
  height: 6px; border-radius: 4px; background: #e7eef8;
}}
.seg.on {{ background: {PRIMARY_DARK}; }}
</style>
"""
# Ensure ERROR color exists
if "ERROR" not in globals(): ERROR = "#E74C3C"

LANE_RETRY_CSS = f"""
<style>
.node-pill.error    {{ background:{ERROR}; color:#fff; }}
.node-pill.retrying {{ background:#FACC15; color:#3A2B00; }}
/* Curved dashed retry arrow under the lane */
.retry-wrap {{ position: relative; height: 40px; margin: -6px 0 8px 0; }}
.retry-svg  {{ width: 100%; height: 100%; }}
.retry-path {{ fill: none; stroke: {ERROR}; stroke-width: 3; stroke-dasharray: 6 6; opacity: .85; }}
</style>
"""
st.markdown(LANE_RETRY_CSS, unsafe_allow_html=True)

st.markdown(OCCUPANCY_CSS, unsafe_allow_html=True)

st.markdown(FANOUT_CSS, unsafe_allow_html=True)

st.markdown(FIX_GHOST_SELECT_INPUT, unsafe_allow_html=True)

st.markdown(FIX_SELECTBOX_CSS, unsafe_allow_html=True)

st.markdown(UPLOAD_CSS, unsafe_allow_html=True)

st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

st.markdown(f"""
<style>
.view-link {{ cursor:pointer; color:{PRIMARY_DARK}; font-weight:700; }}
.view-link:hover {{ text-decoration: underline; }}
.card-details {{ margin-top:6px; }}
.card-details summary::-webkit-details-marker {{ display:none; }}
.fulltext {{
  margin-top:6px; background:#F7FAFF; border:1px solid rgba(22,50,74,0.10);
  border-radius:10px; padding:10px; white-space:pre-wrap; color:{TEXT_DARK};
  max-height:260px; overflow:auto;
}}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<style>
/* Retry arrow styling */
.retry-wrap{ position:relative; height:42px; margin:-8px 0 6px 0; }
.retry-svg { width:100%; height:100%; overflow:visible; }
.retry-glow{ fill:none; stroke:rgba(231,76,60,0.18); stroke-width:7; }
.retry-path{ fill:none; stroke:#E74C3C; stroke-width:2.6; stroke-dasharray:5 4; stroke-linecap:round; }
.retry-head{ fill:#E74C3C; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<style>
/* Wrap the lane and reserve a clean band for the retry overlay */
.lane-wrap{
  position: relative;
  padding-bottom: 26px;    /* space for the arrow */
}

/* Keep pills nice, but hide horizontal scrollbar to avoid the gray track */
.lane{
  display:flex; align-items:center; gap:14px; flex-wrap:nowrap;
  padding:10px 8px 14px 8px; border-radius:12px;
  background:#FAFDFF; border:1px dashed rgba(79,121,177,0.18);
  min-width: 960px;
  overflow-x:hidden;        /* <— key: no scroll track showing */
}

/* Arrow overlay sits below the lane, full width, not clickable */
.retry-overlay{
  position:absolute; left:0; right:0; bottom:2px; height:22px;
  pointer-events:none;
}
.retry-svg{ width:100%; height:100%; overflow:visible; }

/* Pretty arrow */
.retry-glow{ fill:none; stroke:rgba(231,76,60,0.18); stroke-width:7; }
.retry-path{ fill:none; stroke:#E74C3C; stroke-width:2.6; stroke-dasharray:5 4; stroke-linecap:round; }
.retry-head{ fill:#E74C3C; }
</style>
""", unsafe_allow_html=True)


# -------------------------------
# NAV STATE
# -------------------------------
if "page" not in st.session_state:
  st.session_state.page = "home"

def go(page: str):
  st.session_state.page = page
  st.rerun()

# -------------------------------
# PAGES
# -------------------------------
def page_home():
    # Hero card
    st.markdown(
        """
        <div class="hero-wrap">
          <div class="hero">
            <h1>Memo Generation Demo</h1>
            <p>Upload documents → analyze coverage → orchestrate Credit&nbsp;AI → deliver multi-section review memos.</p>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Centered buttons (styled via .primary-btn / .secondary-btn)
    st.markdown('<div class="btn-wrap">', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="primary-btn">', unsafe_allow_html=True)
        if st.button("Upload Documents", key="home_upload", use_container_width=True):
            st.session_state.page = "upload"
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="secondary-btn">', unsafe_allow_html=True)
        if st.button("Review Results", key="home_review", use_container_width=True):
            st.session_state.page = "review"
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def page_upload():
    st.markdown('<div class="form-card">', unsafe_allow_html=True)

    st.header("Upload Documents")
    col1, col2 = st.columns(2)
    with col1:
        rp = st.text_input("Risk Party ID", key="risk_party_id")
    with col2:
        rid = st.text_input("Review ID", key="review_id")

    st.divider()
    files = st.file_uploader(
        "Drag & drop one or more PDF files",
        type=["pdf"], accept_multiple_files=True,
        help="Add all documents you want to include for this review."
    )
    # ... keep your per-file expander + validation + Submit button ...

    documents = []
    if files:
        st.subheader("Document Details")
        for f in files:
            with st.expander(f"{f.name}", expanded=True):
                c1, c2 = st.columns([2, 1.4])
                with c1:
                    doc_type = st.selectbox(
                        "Document Type",
                        DOC_TYPES,
                        key=f"type_{f.name}"
                    )
                    biz_date = st.text_input(
                        "Business Date (YYYY or Q#YYYY, e.g., 2024 or Q22024)",
                        key=f"biz_{f.name}"
                    )
                    if biz_date and not is_valid_business_date(biz_date):
                        st.error("Use YYYY or Q#YYYY (e.g., 2024 or Q22024).")
                with c2:
                    st.caption(f"Size: {len(f.getvalue())/1024:.1f} KB")

                # Read file once; Streamlit-friendly
                data_b64 = base64.b64encode(f.getvalue()).decode("utf-8")

                documents.append({
                    "file_name": f.name,
                    "document_type": doc_type,
                    "business_date": biz_date,
                    "data": data_b64
                })

    # Submit
    submitted = st.button("Submit Documents", type="primary", use_container_width=True)
    if submitted:
        # Validations
        if not rp or not rid:
            st.warning("Please enter both Risk Party ID and Review ID.")
            return
        if not files:
            st.warning("Please upload at least one PDF.")
            return
        bad_dates = [d["file_name"] for d in documents if not is_valid_business_date(d["business_date"])]
        if bad_dates:
            st.warning(f"Please fix business dates for: {', '.join(bad_dates)}.")
            return


        # Save to session for downstream pages
        st.session_state.uploaded_docs = documents
        st.session_state.payload = {
            "risk_party_id": rp,
            "review_id": rid,
            "documents": documents
        }

        st.success("Documents uploaded successfully!")
        st.session_state.page = "process"  # temp: send to placeholder
        st.rerun()

import html

def render_fanout(documents, states):
    def card(d, s):
        fn = html.escape(d.get("file_name",""))
        dt = html.escape(d.get("document_type",""))
        bd = html.escape((d.get("business_date") or "—"))
        status_txt = "Ingesting…" if s == "progress" else ("Ready" if s == "success" else "Queued")
        return (
            f'<div class="doc-chip">'
            f'<h5 title="{fn}">{fn}</h5>'
            f'<div class="doc-meta">Type: {dt}</div>'
            f'<div class="doc-meta">Biz Date: {bd}</div>'
            f'<span class="status-pill {s}">{status_txt}</span>'
            f'</div>'
        )

    inner = "".join(card(d, s) for d, s in zip(documents, states))
    return (
        '<div class="fanout-card">'
        '<div class="fanout-title">Per-document ingest (fan-out)</div>'
        f'<div class="doc-grid">{inner}</div>'
        '</div>'
    )

def page_process():
    st.header("Document Processing")

    # Guard: require uploaded docs
    docs = st.session_state.get("uploaded_docs", [])
    if not docs:
        st.info("No documents found. Please upload documents first.")
        if st.button("Go to Upload", type="primary"):
            st.session_state.page = "upload"; st.rerun()
        return

    # Areas
    lane_area   = st.empty()
    fanout_area = st.empty()
    footer_area = st.empty()

    # Node states
    dp_nodes  = DOC_NODES[:]     # ["Document Upload", ..., "Trigger Evaluation"]
    dp_states = ["pending"] * len(dp_nodes)
    retry_badges = {}   # index -> {"type": "live"|"scar", "label": "↶", "title": "..."}
    event_chips  = []  # strings of small chips under the lane

    def paint_lane(ingested=None, total=None, payloads=None, payloads_total=None):
      labels = dp_nodes[:]
      if ingested is not None and total is not None:
          labels[4] = f"Async DB Ingestion  {ingested}/{total}"
      if payloads is not None and payloads_total is not None:
          labels[5] = f"Trigger Evaluation  {payloads}/{payloads_total}"

      html_lane = lane_html(
          "📄 Document Processing",
          labels,
          dp_states,
          retry_badges=retry_badges,                    # <-- badges/scars
          events_html="".join(
              f'<span class="event-chip">{c}</span>' for c in event_chips
          ),                                            # <-- chips row
      )
      lane_area.markdown(f'<div class="board">{html_lane}</div>', unsafe_allow_html=True)




    # 1–4: move as a bundle (happy path)
    paint_lane()
    for i in range(4):  # Document Upload → Proxy Document Retriever
        dp_states[i] = "progress"; paint_lane(); wait("dp_progress")
        dp_states[i] = "success";  paint_lane(); wait("dp_success")

    # 5: Async DB Ingestion (fan-out)
    dp_states[4] = "progress"
    paint_lane(ingested=0, total=len(docs))

    # Fan-out grid
    doc_states = ["pending"] * len(docs)
    fanout_area.markdown(render_fanout(docs, doc_states), unsafe_allow_html=True)

    # Parallel Trigger Evaluation
    trigger_started = False
    payloads_sent   = 0

    done = 0
    failing_active = (len(docs) > BULK_FAIL_THRESHOLD)

    for idx in range(len(docs)):
        # this doc starts ingesting
        doc_states[idx] = "progress"
        fanout_area.markdown(render_fanout(docs, doc_states), unsafe_allow_html=True)
        wait("fo_progress")

        if failing_active and idx == BULK_FAIL_DOC_INDEX:
          prev_node = 3  # "Proxy Document Retriever"

          # 1) Async DB error (red) + show "live" retry badge and event chip
          dp_states[4] = "error"
          retry_badges[4] = {"type": "live", "label": "↶", "title": "Retrying via Proxy"}
          event_chips.append('<span class="red">↶</span> Async DB → Proxy')
          paint_lane(ingested=done, total=len(docs))

          # 2) previous node re-processes (yellow -> green) while Async stays red
          dp_states[prev_node] = "progress"; paint_lane(ingested=done, total=len(docs)); wait("dp_progress")
          dp_states[prev_node] = "success";  paint_lane(ingested=done, total=len(docs)); wait("dp_success")

          # 3) Async DB retries (yellow) and this doc completes
          dp_states[4] = "progress"; paint_lane(ingested=done, total=len(docs)); wait("fo_progress")

          doc_states[idx] = "success"
          done += 1
          event_chips.append('<span class="green">✓</span> Recovered')
          retry_badges[4] = {"type": "scar", "title": "1 retry on this stage"}  # persist scar
          paint_lane(ingested=done, total=len(docs))
          fanout_area.markdown(render_fanout(docs, doc_states), unsafe_allow_html=True)
          wait("fo_success")

          failing_active = False

          # (optional) early Trigger Evaluation handling
          if (not trigger_started) and (done >= min(TRIGGER_START_AFTER, len(docs))):
              trigger_started = True
              dp_states[5] = "progress"
              payloads_sent = 1
              paint_lane(ingested=done, total=len(docs), payloads=payloads_sent, payloads_total=TOTAL_INTENTS)
              wait("tr_start")
          elif trigger_started and payloads_sent < TOTAL_INTENTS:
              payloads_sent += 1
              paint_lane(ingested=done, total=len(docs), payloads=payloads_sent, payloads_total=TOTAL_INTENTS)
              wait("tr_tick")
          else:
              paint_lane(ingested=done, total=len(docs))

          continue  # IMPORTANT: skip the normal success path for this doc


        # ---- normal success path (no failure) ----
        doc_states[idx] = "success"
        done += 1

        # Start TE once threshold reached (or if threshold > total, we'll start later)
        if (not trigger_started) and (done >= min(TRIGGER_START_AFTER, len(docs))):
            trigger_started = True
            dp_states[5] = "progress"
            payloads_sent = 1   # first payload goes out as we start
            paint_lane(ingested=done, total=len(docs),
                      payloads=payloads_sent, payloads_total=TOTAL_INTENTS)
            wait("tr_start")
        else:
            # If already started, continue sending payloads while docs keep ingesting
            if trigger_started and payloads_sent < TOTAL_INTENTS:
                payloads_sent += 1
                paint_lane(ingested=done, total=len(docs),
                          payloads=payloads_sent, payloads_total=TOTAL_INTENTS)
                wait("tr_tick")
            else:
                # just repaint the ingest counter if TE hasn't started yet
                paint_lane(ingested=done, total=len(docs))

        # repaint fanout after this doc completes
        fanout_area.markdown(render_fanout(docs, doc_states), unsafe_allow_html=True)
        wait("fo_success")



    # Mark ingest node success after all docs ready
    dp_states[4] = "success"
    paint_lane(ingested=len(docs), total=len(docs),
               payloads=(payloads_sent if trigger_started else None),
               payloads_total=(TOTAL_INTENTS if trigger_started else None))

    # If TE never started (e.g., docs < threshold), start now
    if not trigger_started:
        trigger_started = True
        dp_states[5] = "progress"
        paint_lane(ingested=len(docs), total=len(docs),
                   payloads=payloads_sent, payloads_total=TOTAL_INTENTS)
        wait("tr_start")

    # Finish sending any remaining payloads
    while payloads_sent < TOTAL_INTENTS:
        payloads_sent += 1
        paint_lane(ingested=len(docs), total=len(docs),
                   payloads=payloads_sent, payloads_total=TOTAL_INTENTS)
        wait("tr_tick")

    # TE completes
    dp_states[5] = "success"
    paint_lane(ingested=len(docs), total=len(docs),
               payloads=payloads_sent, payloads_total=TOTAL_INTENTS)
    wait("tr_finish")

        # ===== CREDIT AI (happy path, asynchronous per-payload) =====
    st.subheader("Credit AI")
    ai_lane_area  = st.empty()
    occ_area      = st.empty()
    payloads_area = st.empty()
    results_map   = {}   # name -> result dict (llm_response, timestamp, etc.)

    payloads_names = PAYLOAD_SECTION_NAMES[:]                 # ["Business Description","Recent Developments","ABL"]
    payloads_idx   = [0] * TOTAL_INTENTS                      # current node index per payload (0..last)
    last_idx       = len(AI_NODES) - 1

    # Initial paint
    _paint_ai_lane(ai_lane_area, payloads_idx)
    occ_area.markdown(_render_occupancy_row(payloads_idx, TOTAL_INTENTS), unsafe_allow_html=True)
    payloads_area.markdown(_render_payload_cards(payloads_names, payloads_idx, results_map), unsafe_allow_html=True)


    # Set up per-payload ETAs to finish their current stage
    # (time to move from idx -> idx+1). If a payload is already at last_idx, ETA=inf.
    INF  = 10**9
    etas = [_stage_duration(0) for _ in range(TOTAL_INTENTS)]

    # Event loop: at each step, advance the payload with the smallest ETA
    while True:
        # check if all delivered
        if all(i >= last_idx for i in payloads_idx):
            break

        # pick next event (payload that finishes its current stage first)
        active = [(p, t) for p, t in enumerate(etas) if payloads_idx[p] < last_idx]
        p_next, dt = min(active, key=lambda x: x[1])

        # wait dt and subtract from others so clocks stay in sync
        _sleep_smooth(dt)
        for p in range(TOTAL_INTENTS):
            if payloads_idx[p] < last_idx:
                etas[p] = max(0.0, etas[p] - dt)

        # advance that payload one stage
        payloads_idx[p_next] += 1
        if payloads_idx[p_next] < last_idx:
            # set ETA for its new current stage
            etas[p_next] = _stage_duration(payloads_idx[p_next])
        else:
            etas[p_next] = INF  # delivered; no more events
            name = payloads_names[p_next]
            if name not in results_map:
              results_map[name] = mock_fetch_intent_result(name)

        # repaint after the event
        _paint_ai_lane(ai_lane_area, payloads_idx)
        occ_area.markdown(_render_occupancy_row(payloads_idx, TOTAL_INTENTS), unsafe_allow_html=True)
        payloads_area.markdown(_render_payload_cards(payloads_names, payloads_idx, results_map), unsafe_allow_html=True)

    # final settle & message
    _sleep_smooth(SIM.get("ai_settle", 0.35) * SPEED_FACTOR)
    st.success("All payloads delivered. Output Delivery complete.")



def page_review():
  st.header("Review Results")
  st.info("Placeholder: results review UI coming next.") 

# -------------------------------
# ROUTER
# -------------------------------
if st.session_state.page == "home":
    page_home()
elif st.session_state.page == "upload":
    page_upload()
elif st.session_state.page == "process":
    page_process()
elif st.session_state.page == "review":
    page_review()