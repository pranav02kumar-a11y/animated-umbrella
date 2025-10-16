from contextlib import contextmanager

# Stack of temporary speed profiles (LIFO). Each item can include:
#  - "dp": {node_index -> multiplier}
#  - "fo": {doc_index -> multiplier}
#  - "ai": {stage_index -> multiplier}
#  - "ai_per_payload_stage": {(payload_index, stage_index) -> multiplier}
SPEED_OVERRIDES = []

def _apply_overrides(seconds: float, *, kind: str = None, index=None, payload_idx=None, stage_idx=None) -> float:
    """Apply all active SPEED_OVERRIDES to a given duration."""
    mult = 1.0
    for ov in SPEED_OVERRIDES:
        if kind == "dp" and index is not None:
            mult *= ov.get("dp", {}).get(index, 1.0)
        elif kind == "fo" and index is not None:
            mult *= ov.get("fo", {}).get(index, 1.0)
        elif kind == "ai":
            if stage_idx is not None:
                mult *= ov.get("ai", {}).get(stage_idx, 1.0)
            if payload_idx is not None and stage_idx is not None:
                mult *= ov.get("ai_per_payload_stage", {}).get((payload_idx, stage_idx), 1.0)
    return seconds * mult

@contextmanager
def speed_profile(*, dp=None, fo=None, ai=None, ai_per_payload_stage=None):
    """Temporarily adjust speed of specific nodes/stages while inside the context."""
    SPEED_OVERRIDES.append({
        "dp": dp or {},
        "fo": fo or {},
        "ai": ai or {},
        "ai_per_payload_stage": ai_per_payload_stage or {},
    })
    try:
        yield
    finally:
        SPEED_OVERRIDES.pop()

# --- Drop-in wait wrappers that respect overrides ---
def wait_dp(node_index: int, phase: str):     # phase: "progress" | "success"
    dur = SIM[f"dp_{phase}"] * SPEED_FACTOR
    dur = _apply_overrides(dur, kind="dp", index=node_index)
    _sleep_smooth(dur)

def wait_fo(doc_index: int, phase: str):      # phase: "progress" | "success"
    key = "fo_progress" if phase == "progress" else "fo_success"
    dur = SIM[key] * SPEED_FACTOR
    dur = _apply_overrides(dur, kind="fo", index=doc_index)
    _sleep_smooth(dur)

def wait_ai_phase(sim_key: str, *, payload_idx=None, stage_idx=None):
    # sim_key is one of: "ai_progress", "ai_advance", "ai_settle" (you already use these)
    dur = SIM.get(sim_key, 0.5) * SPEED_FACTOR
    dur = _apply_overrides(dur, kind="ai", payload_idx=payload_idx, stage_idx=stage_idx)
    _sleep_smooth(dur)

  ##################################

def _stage_duration(stage: int, payload_idx=None) -> float:
    """Randomized dwell time for a payload at a given stage, scaled by SPEED_FACTOR and overrides."""
    base = AI_STAGE_BASE[stage]
    jitter = random.uniform(0.75, 1.35)
    dur = base * jitter * SPEED_FACTOR
    # Apply any AI overrides (global stage and/or (payload,stage) specific)
    dur = _apply_overrides(dur, kind="ai", payload_idx=payload_idx, stage_idx=stage)
    return dur

###############################

# Initial ETAs
etas = [_stage_duration(0, p) for p in range(TOTAL_INTENTS)]

# When a payload advances:
if payloads_idx[p_next] < last_idx:
    etas[p_next] = _stage_duration(payloads_idx[p_next], p_next)
else:
    etas[p_next] = INF


###########################
if failing_active and idx == BULK_FAIL_DOC_INDEX:
    prev_node = 3  # "Proxy Document Retriever"

    # While failing: slow Async DB (node 4), speed the others a bit, and
    # optionally slow this specific doc's FO progress slightly for dramatization.
    with speed_profile(
        dp={4: 1.8, 0: 0.8, 1: 0.8, 2: 0.8, 3: 0.8, 5: 0.8},
        fo={idx: 1.2},
    ):
        # Step 1: show error on Async DB with back arrow to Proxy
        dp_states[4] = "error"
        retry_badges[4] = {"type": "live", "label": "↶", "title": "Retrying via Proxy"}
        event_chips.append('<span class="red">↑</span> Async DB → Proxy')
        arrow_back_idx, arrow_back_live = 3, True
        paint_lane(ingested=done, total=len(docs))

        # Step 2: re-process Proxy (speeded up)
        dp_states[prev_node] = "progress"; paint_lane(ingested=done, total=len(docs)); wait_dp(prev_node, "progress")
        dp_states[prev_node] = "success";  paint_lane(ingested=done, total=len(docs)); wait_dp(prev_node, "success")

        # Step 3: Async DB retries (slowed)
        dp_states[4] = "progress"; paint_lane(ingested=done, total=len(docs)); wait_dp(4, "progress")

        # doc completes (fanout timing respects FO override for this doc)
        doc_states[idx] = "success"; done += 1
        event_chips.append('<span class="green">✓</span> Recovered')
        retry_badges[4] = {"type": "scar", "title": "1 retry on this stage"}
        arrow_back_idx, arrow_back_live = None, False

        paint_lane(ingested=done, total=len(docs))
        fanout_area.markdown(render_fanout(docs, doc_states), unsafe_allow_html=True)
        wait_fo(idx, "success")

        failing_active = False

        # Trigger Evaluation pacing unchanged (optional to keep as-is)
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

    continue  # IMPORTANT

#####################################
if (
    credit_fail_active and not credit_fail_consumed
    and abl_payload_index is not None
    and p_next == abl_payload_index
    and payloads_idx[p_next] == 4
):
    # Slow just ABL on stage 4 (invocation), speed other stages slightly
    with speed_profile(
        ai={0: 0.85, 1: 0.85, 2: 0.85, 3: 0.85, 5: 0.85},
        ai_per_payload_stage={ (abl_payload_index, 4): 1.9 }
    ):
        # 1) show error + live badge + back arrow
        ai_state_overrides[4] = "error"
        ai_retry_badges[4] = {"type": "live", "label": "↶", "title": "Retrying via Context"}
        ai_event_chips.append('↶ Credit AI → Context • ABL')
        ai_arrow_back_idx, ai_arrow_back_live = 3, True

        card_overrides["ABL"] = {"pill": "retrying", "at": "Retrying via Context (ABL)"}
        paint_ai()
        wait_ai_phase("ai_progress", payload_idx=abl_payload_index, stage_idx=4)

        # 2) re-process Context (stage 3) while invocation remains errored
        ai_state_overrides[3] = "progress"; paint_ai()
        wait_ai_phase("ai_progress", payload_idx=abl_payload_index, stage_idx=3)

        ai_state_overrides[3] = "success";  paint_ai()
        wait_ai_phase("ai_settle", payload_idx=abl_payload_index, stage_idx=3)

        # 3) retry succeeds: clear error, keep scar
        ai_state_overrides.pop(4, None)
        ai_retry_badges[4] = {"type": "scar", "title": "1 retry on this stage"}
        ai_event_chips.append('✓ Recovered (ABL)')
        ai_arrow_back_idx, ai_arrow_back_live = None, False
        card_overrides.pop("ABL", None)

        credit_fail_consumed = True
        paint_ai()
        # continue to normal loop repaint



























import streamlit as st
import re
import base64

from datetime import datetime
import html, random
import time as _time
# import requests
# from auth import get_authenticated_headers

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

# def get_intent_result(intent, risk_party_id, review_id):
#     headers = get_authenticated_headers()
#     base_url = "url"
#     response = requests.get(f"{base_url}/{risk_party_id}/{review_id}/{intent}", headers=headers)

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
SPEED_FACTOR = 1.0

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
ERROR          = "#E74C3C"
PENDING        = "#98A6B3"
SUCCESS        = "#34C759"
PROGRESS       = "#F4C542"

DOC_NODES = [
        "Document Upload",
        "S3 Upload",
        "Section Coverage Analysis",
        "Proxy Document Retriever",
        "Async DB Ingestion",
        "Trigger Evaluation", 
    ]

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

def _ai_counts(payloads_idx, n_nodes):
    counts = [0]*n_nodes
    for idx in payloads_idx:
        counts[min(idx, n_nodes-1)] += 1
    return counts

def _paint_ai_lane(
    ai_lane_area,
    payloads_idx,
    retry_badges=None,
    event_chips=None,
    back_edge_idx=None,
    back_live=False,
    override_states=None,
):
    last = len(AI_NODES) - 1
    counts = _ai_counts(payloads_idx, len(AI_NODES))
    states, labels = [], []

    for j, name in enumerate(AI_NODES):
        if override_states and j in override_states:
            state = override_states[j]
        else:
            at_j  = counts[j]
            after = sum(1 for x in payloads_idx if x > j)
            if j == last:
                state = "success" if all(x >= last for x in payloads_idx) else ("progress" if at_j > 0 else "pending")
            else:
                state = "success" if after == len(payloads_idx) else ("progress" if at_j > 0 else "pending")
        states.append(state)
        labels.append(name)

    events_html = "".join(f'<span class="event-chip">{c}</span>' for c in (event_chips or []))
    html_block = (
        '<div class="board">' +
        lane_html(
            "Credit AI",
            labels,
            states,
            retry_badges=retry_badges,
            events_html=events_html,
            back_edge_idx=back_edge_idx,
            back_live=back_live,
        ) +
        '</div>'
    )
    ai_lane_area.markdown(html_block, unsafe_allow_html=True)


    chips_html = "".join(event_chips or [])
    html_lane = lane_html(
        "Credit AI",
        labels,
        states,
        retry_badges=retry_badges,
        events_html=chips_html,
        back_edge_idx=back_edge_idx,
        back_live=back_live,
    )
    ai_lane_area.markdown(f'<div class="board">{html_lane}</div>', unsafe_allow_html=True)


def _render_occupancy_row(payloads_idx, total_payloads):
    counts = _ai_counts(payloads_idx, len(AI_NODES))
    cells = []
    for c in counts:
        dots = ''.join(f'<span class="occ-dot {"on" if k < c else ""}"></span>' for k in range(total_payloads))
        cells.append(f'<div class="occ-cell">{dots}</div>')
    return f'<div class="occ-strip">{"".join(cells)}</div>'

def _render_payload_cards(names, idxs, results_map, card_overrides=None):
    card_overrides = card_overrides or {}
    last = len(AI_NODES) - 1
    cards = []
    for name, idx in zip(names, idxs):
        segs = ''.join(f'<span class="seg {"on" if s <= idx else ""}"></span>' for s in range(len(AI_NODES)))
        at_name = AI_NODES[min(idx, last)]
        done = idx >= last

        ov = card_overrides.get(name, {})
        pill = ov.get("pill") or ("success" if done else "progress")

        # Body text: allow an override line (e.g., "Retrying via Context")
        if done and name in results_map and not ov.get("at"):
            res = results_map[name]
            ts = html.escape(res.get("timestamp", ""))
            full = html.escape(res.get("llm_response", ""))
            snippet = html.escape(make_snippet(res.get("llm_response", "")))
            body = (
                f'<div class="doc-meta">Delivered • {ts}</div>'
                f'<div class="doc-meta">{snippet}</div>'
                f'<details class="card-details"><summary class="view-link">View full response</summary>'
                f'<div class="fulltext">{full}</div></details>'
            )
        else:
            at_line = ov.get("at") or f'At: {html.escape(at_name)}'
            body = f'<div class="doc-meta">{html.escape(at_line)}</div>'

        cards.append(
            f'<div class="doc-chip">'
            f'<h5>{html.escape(name)}</h5>'
            f'{body}'
            f'<div class="segbar">{segs}</div>'
            f'<span class="status-pill {pill}">{"Done" if pill=="success" else ("Retrying…" if pill=="retrying" else ("Error" if pill=="error" else "Processing"))}</span>'
            f'</div>'
        )

    return (
        '<div class="fanout-card">'
        '<div class="fanout-title">Per-intent payloads (parallel)</div>'
        f'<div class="doc-grid">{"".join(cards)}</div>'
        '</div>'
    )

st.markdown("""
<style>
/* --- Review page: hide rogue unlabeled TextInput (prevents blank full-width pill) --- */
.form-card [data-testid="stTextInput"]:has([data-testid="stWidgetLabel"] p:empty) {
  display: none !important;
  margin: 0 !important;
  padding: 0 !important;
  border: 0 !important;
}
/* Be safe: if the label exists but is whitespace-only, hide it too */
.form-card [data-testid="stWidgetLabel"] p:empty { display:none !important; }
</style>
""", unsafe_allow_html=True)

LANE_BASE_CSS = f""" 
<style>
.board {{
  background: rgba(255, 255, 255, 0.98);
  border: 1px solid rgba(22, 50, 74, 0.08);
  border-radius: 16px;
  box-shadow: 0 8px 24px rgba(22, 50, 74, 0.08);
  padding: 14px 16px;
}}
.group-title {{
  font-weight: 800; color: {PRIMARY}; margin: 4px 0 8px 4px; letter-spacing: .2px;
}}
.lane {{
  display: flex; align-items: center; gap: 14px; flex-wrap: nowrap;
  overflow-x: auto; padding: 10px 8px 14px 8px; border-radius: 12px;
  background: #FAFDFF; border: 1px dashed rgba(79, 121, 177, 0.18);
}}
.node-wrap {{ position: relative; display: inline-block; }}
.node-pill {{
  flex-shrink: 0; min-width: 160px; text-align: center; white-space: nowrap;
  padding: 10px 14px; border-radius: 999px; font-weight: 700; color: #173044;
  background: #EEF3F7; border: 1px solid rgba(25, 53, 74, 0.08);
  transition: background-color .6s ease, box-shadow .6s ease, border-color .6s ease, color .6s ease;
}}
.node-pill.pending  {{ background: #EEF3F7; color: #41515C; }}
.node-pill.progress {{ background: {PROGRESS}; color: #132C3C; }}
.node-pill.success  {{ background: {SUCCESS};  color: #06220E;  }}

.arrow-flex {{
  flex: 1 1 0; display: flex; align-items: center; justify-content: center;
  min-width: 24px; font-size: 22px; color: rgba(60,84,96,0.65); user-select: none;
}}
.arrow-back {{ color:#E74C3C; text-shadow:0 0 8px rgba(231,76,60,0.22); }}
.arrow-back.pulse {{ animation: backpulse .9s ease-in-out infinite; }}

@keyframes backpulse {{
  0%   {{ transform: translateY(0); }}
  50%  {{ transform: translateY(-1px); }}
  100% {{ transform: translateY(0); }}
}}
</style>
"""

st.markdown(LANE_BASE_CSS, unsafe_allow_html=True)

BADGES_AND_EVENTS_CSS = f"""
<style>
/* Anchor badges to each pill */
.node-wrap {{ position: relative; display: inline-block; }}

.retry-badge {{
  position: absolute; top: -8px; right: -8px;
  background: {ERROR}; color: #fff; font-weight: 800;
  border-radius: 10px; padding: 2px 6px; font-size: 11px; line-height: 1;
  box-shadow: 0 4px 10px rgba(231,76,60,0.28);
}}

.retry-scar {{
  position: absolute; top: -6px; right: -6px;
  width: 10px; height: 10px; border-radius: 50%;
  background: #A8B6C8; box-shadow: 0 0 0 2px rgba(168,182,200,0.25);
}}

/* Row of event chips under the lane */
.event-row {{ margin: 6px 2px 0; display: flex; gap: 8px; flex-wrap: wrap; }}
.event-chip {{
  background:#F3F7FD; color:#435768; border:1px solid rgba(22,50,74,0.10);
  padding: 4px 8px; border-radius: 999px; font-size:12px; font-weight:700;
}}
.event-chip .red   {{ color: {ERROR}; }}
.event-chip .green {{ color: #2E7D32; }}
</style>
"""
st.markdown(BADGES_AND_EVENTS_CSS, unsafe_allow_html=True)

st.markdown("""
<style>
/* Make "Retrying…" pills red (card-level only) */
.status-pill.retrying { background:#E74C3C; color:#ffffff; }
.status-pill.error    { background:#E74C3C; color:#ffffff; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<style>
/* Soft pulse for in-progress lane nodes (affects both pipelines) */
@keyframes pillPulse {
  0%   { transform: translateZ(0) scale(1.00); box-shadow: 0 0 0 0 rgba(255,149,0,.35); }
  50%  { transform: translateZ(0) scale(1.035); box-shadow: 0 0 0 7px rgba(255,149,0,.10); }
  100% { transform: translateZ(0) scale(1.00); box-shadow: 0 0 0 0 rgba(255,149,0,0); }
}
.node-pill.progress {
  animation: pillPulse 1.25s ease-in-out infinite;
  will-change: transform, box-shadow;
  border-color: rgba(255,149,0,.55);
}

/* If you ever mark a lane node as "retrying", give it the same pulse */
.node-pill.retrying {
  animation: pillPulse 1.25s ease-in-out infinite;
  will-change: transform, box-shadow;
}

/* Respect reduced motion prefs */
@media (prefers-reduced-motion: reduce) {
  .node-pill.progress,
  .node-pill.retrying { animation: none; }
}
</style>
""", unsafe_allow_html=True)



def lane_html(
    title,
    nodes,
    states,
    retry_badges=None,
    events_html="",
    back_edge_idx=None,   # edge index between nodes[i] and nodes[i+1]
    back_live=False,
):
    # pills (with optional badges/scars)
    pill_wrapped = []
    for i, (label, s) in enumerate(zip(nodes, states)):
        pill = f'<div class="node-pill {s}">{html.escape(label)}</div>'
        badge = ""
        if retry_badges and i in retry_badges:
            b = retry_badges[i]
            if b.get("type") == "live":
                badge = f'<span class="retry-badge" title="{html.escape(b.get("title","Retry"))}">{html.escape(b.get("label","↶"))}</span>'
            elif b.get("type") == "scar":
                badge = f'<span class="retry-scar" title="{html.escape(b.get("title","1 retry"))}"></span>'
        pill_wrapped.append(f'<span class="node-wrap">{pill}{badge}</span>')

    # interleave with elastic arrows (arrow spans grow to fill width)
    segments = []
    for i in range(len(nodes)):
        segments.append(pill_wrapped[i])
        if i < len(nodes) - 1:
            if back_edge_idx is not None and i == back_edge_idx:
                cls = "arrow-flex arrow-back pulse" if back_live else "arrow-flex arrow-back"
                segments.append(f'<span class="{cls}">←</span>')
            else:
                segments.append('<span class="arrow-flex">→</span>')

    html_lane = (
      '<div class="group-title">' + html.escape(title) + '</div>'
      '<div class="lane pipeline">' + "".join(segments) + '</div>'   # <-- add pipeline
    )
    if events_html:
        html_lane += f'<div class="event-row">{events_html}</div>'
    return html_lane


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

LANE_RETRY_CSS = f"""
<style>
.node-pill.error   {{ background: {ERROR}; color: #fff; }}
.node-pill.retrying {{ background:#FACC15; color: #3A2B00;}}
/* Curved dashed retry arrow under the lane */
.retry-wrap {{ position: relative; height: 40px; margin: -6px 0 8px 0;}}
.retry-svg {{ width: 100%; height: 100%;}}
.retry-path {{ fill: none; stroke: {ERROR}; stroke-width: 3; stroke-dasharray: 6 6;opacity: .85;}}
</style>"""

st.markdown(LANE_RETRY_CSS, unsafe_allow_html=True)

st.markdown(OCCUPANCY_CSS, unsafe_allow_html=True)

st.markdown(FANOUT_CSS, unsafe_allow_html=True)

st.markdown(FIX_GHOST_SELECT_INPUT, unsafe_allow_html=True)

st.markdown(FIX_SELECTBOX_CSS, unsafe_allow_html=True)

st.markdown(UPLOAD_CSS, unsafe_allow_html=True)

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
            f'<div class="doc-meta">Business Date: {bd}</div>'
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
    retry_badges = {}   # existing badges map (keep)
    event_chips  = []   # existing chips (keep)
    arrow_back_idx  = None   # ← which edge to flip (e.g., 3 for Proxy→Async)
    arrow_back_live = False  # ← pulse while retrying

    def paint_lane(ingested=None, total=None, payloads=None, payloads_total=None):
      labels = dp_nodes[:]
      if ingested is not None and total is not None:
          labels[4] = f"Async DB Ingestion  {ingested}/{total}"
      if payloads is not None and payloads_total is not None:
          labels[5] = f"Trigger Evaluation  {payloads}/{payloads_total}"

      html_lane = lane_html(
          "Document Processing",
          labels,
          dp_states,
          retry_badges=retry_badges,
          events_html="".join(f'<span class="event-chip">{c}</span>' for c in event_chips),
          back_edge_idx=arrow_back_idx,          # ← pass current override
          back_live=arrow_back_live,             # ← pulse during retry
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

          # Step 1: Async DB error + show live badge + flip arrow backward (Proxy←Async)
          dp_states[4] = "error"
          retry_badges[4] = {"type": "live", "label": "↶", "title": "Retrying via Proxy"}
          event_chips.append('<span class="red">↑</span> Async DB → Proxy')

          arrow_back_idx  = 3        # edge between nodes[3] and nodes[4]
          arrow_back_live = True     # pulse while retrying
          paint_lane(ingested=done, total=len(docs))

          # Step 2: Proxy re-processes (yellow → green) while Async DB stays red
          dp_states[prev_node] = "progress"; paint_lane(ingested=done, total=len(docs)); wait("dp_progress")
          dp_states[prev_node] = "success";  paint_lane(ingested=done, total=len(docs)); wait("dp_success")

          # Step 3: Async DB retries (yellow), then this doc completes
          dp_states[4] = "progress"; paint_lane(ingested=done, total=len(docs)); wait("fo_progress")

          doc_states[idx] = "success"
          done += 1
          event_chips.append('<span class="green">✓</span> Recovered')
          retry_badges[4] = {"type": "scar", "title": "1 retry on this stage"}  # scar persists

          # Clear the temporary back arrow now that Proxy succeeded
          arrow_back_idx  = None
          arrow_back_live = False

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

        # ===== CREDIT AI (with ABL retry if fail.pdf present) =====
    st.subheader("Credit AI")

    # UI areas
    ai_lane_area  = st.empty()
    occ_area      = st.empty()
    payloads_area = st.empty()

    # Data/result state
    results_map   = {}
    payloads_names = PAYLOAD_SECTION_NAMES[:]   # ["Business Description","Recent Developments","ABL"]
    payloads_idx   = [0] * TOTAL_INTENTS
    last_idx       = len(AI_NODES) - 1

    # --- visuals state for retry UX on the AI lane ---
    card_overrides      = {}   # per-card pill/line overrides
    ai_retry_badges     = {}   # node index -> {type:"live"/"scar", ...}
    ai_event_chips      = []   # plain strings; rendered as chips in paint_ai()
    ai_arrow_back_idx   = None # which edge to show back arrow (i between nodes[i] and nodes[i+1])
    ai_arrow_back_live  = False
    ai_state_overrides  = {}   # node index -> "error"/"progress"/"success" (overrides lane state)

    # --- detect trigger: any uploaded file literally named "fail.pdf" ---
    docs = st.session_state.get("payload", {}).get("documents", [])
    credit_fail_active   = any((d.get("file_name","").lower() == "fail.pdf") for d in docs)
    credit_fail_consumed = False
    try:
        abl_payload_index = payloads_names.index("ABL")
    except ValueError:
        abl_payload_index = None

    # ---- helpers ---------------------------------------------------------------
    def _ai_states_from_payloads(idxs, override_states=None):
        """Compute lane node states from payload positions with optional per-node overrides."""
        override_states = override_states or {}
        counts = _ai_counts(idxs, len(AI_NODES))
        last   = len(AI_NODES) - 1
        states = []
        for j in range(len(AI_NODES)):
            if j in override_states:
                states.append(override_states[j])
                continue
            at_j  = counts[j]
            after = sum(1 for x in idxs if x > j)
            if j == last:
                state = "success" if all(x >= last for x in idxs) else ("progress" if at_j > 0 else "pending")
            else:
                state = "success" if after == len(idxs) else ("progress" if at_j > 0 else "pending")
            states.append(state)
        return states

    def paint_ai():
        """One paint for lane + occupancy + cards (chips are styled like the ingest lane)."""
        states      = _ai_states_from_payloads(payloads_idx, ai_state_overrides)
        labels      = AI_NODES[:]
        events_html = "".join(f'<span class="event-chip">{c}</span>' for c in ai_event_chips)

        lane_html_block = lane_html(
            "Credit AI",
            labels,
            states,
            retry_badges=ai_retry_badges,
            events_html=events_html,
            back_edge_idx=ai_arrow_back_idx,
            back_live=ai_arrow_back_live,
        )
        ai_lane_area.markdown(f'<div class="board">{lane_html_block}</div>', unsafe_allow_html=True)
        occ_area.markdown(_render_occupancy_row(payloads_idx, TOTAL_INTENTS), unsafe_allow_html=True)
        payloads_area.markdown(
            _render_payload_cards(payloads_names, payloads_idx, results_map, card_overrides),
            unsafe_allow_html=True
        )

    # Initial paint
    paint_ai()

    # Per-payload ETAs to finish current stage (idx -> idx+1)
    INF  = 10**9
    etas = [_stage_duration(0) for _ in range(TOTAL_INTENTS)]

    # ---- event loop ------------------------------------------------------------
    while True:
        if all(i >= last_idx for i in payloads_idx):
            break

        # next event: payload with smallest ETA among those not delivered
        active = [(p, t) for p, t in enumerate(etas) if payloads_idx[p] < last_idx]
        p_next, dt = min(active, key=lambda x: x[1])

        _sleep_smooth(dt)
        for p in range(TOTAL_INTENTS):
            if payloads_idx[p] < last_idx:
                etas[p] = max(0.0, etas[p] - dt)

        # advance the chosen payload one stage
        payloads_idx[p_next] += 1

        if payloads_idx[p_next] < last_idx:
            etas[p_next] = _stage_duration(payloads_idx[p_next])
        else:
            etas[p_next] = INF  # delivered
            name = payloads_names[p_next]
            if name not in results_map:
                results_map[name] = mock_fetch_intent_result(name)

        # --- inject a one-time failure at "Credit AI Invocation" for ABL when fail.pdf uploaded ---
        # Node indexes: 0 Receive, 1 Prompt, 2 Download, 3 Context, 4 Credit AI Invocation, 5 Output
        if (
            credit_fail_active and not credit_fail_consumed
            and abl_payload_index is not None
            and p_next == abl_payload_index
            and payloads_idx[p_next] == 4  # just reached "Credit AI Invocation"
        ):
            # 1) Invocation pill shows error + live badge; show animated back arrow to Context
            ai_state_overrides[4] = "error"
            ai_retry_badges[4] = {"type": "live", "label": "↶", "title": "Retrying via Context"}
            ai_event_chips.append('↶ Credit AI → Context • ABL')
            ai_arrow_back_idx  = 3   # edge between Context (3) and Invocation (4)
            ai_arrow_back_live = True

            # ABL card shows yellow pill + explicit retry message
            card_overrides["ABL"] = {"pill": "retrying", "at": "Retrying via Context"}

            paint_ai()
            _sleep_smooth(SIM.get("ai_progress", 0.80) * SPEED_FACTOR)

            # 2) Re-process prior node (Context) while Invocation stays red
            ai_state_overrides[3] = "progress"; paint_ai(); _sleep_smooth(SIM.get("ai_settle", 0.35) * SPEED_FACTOR)
            ai_state_overrides[3] = "success";  paint_ai(); _sleep_smooth(SIM.get("ai_settle", 0.35) * SPEED_FACTOR)

            # 3) Retry succeeds → clear error, keep scar, stop back arrow, log recovery
            ai_state_overrides.pop(4, None)
            ai_retry_badges[4] = {"type": "scar", "title": "1 retry on this stage"}
            ai_event_chips.append('✓ Recovered (ABL)')
            ai_arrow_back_idx  = None
            ai_arrow_back_live = False
            card_overrides.pop("ABL", None)  # remove the temporary yellow pill / line

            credit_fail_consumed = True
            paint_ai()
            continue  # still repaint at loop bottom, but we're already fresh

        # Normal repaint after each hop
        paint_ai()

    # final settle & message
    _sleep_smooth(SIM.get("ai_settle", 0.35) * SPEED_FACTOR)
    st.success("All payloads delivered. Output Delivery complete.")

def page_review():
    st.header("Review Results")

    # Card wrapper like Upload page for consistency
    st.markdown('<div class="form-card">', unsafe_allow_html=True)

    # Prefill from last payload if available
    rp_default = st.session_state.get("payload", {}).get("risk_party_id", "")
    rid_default = st.session_state.get("payload", {}).get("review_id", "")

    col1, col2 = st.columns(2)
    with col1:
        rp = st.text_input("Risk Party ID", value=rp_default, key="review_risk_party_id")
    with col2:
        rid = st.text_input("Review ID", value=rid_default, key="review_review_id")

    fetch = st.button("Fetch Sections", type="primary", use_container_width=True)

    st.markdown('</div>', unsafe_allow_html=True)  # close form-card

    # Use session cache so the cards persist after first fetch
    if "review_results" not in st.session_state:
        st.session_state.review_results = None

    if fetch:
        if not rp or not rid:
            st.warning("Please enter both Risk Party ID and Review ID.")
            return
        # For now we use the mock function for all three sections
        results = {name: mock_fetch_intent_result(name) for name in PAYLOAD_SECTION_NAMES}
        st.session_state.review_results = {
            "risk_party_id": rp,
            "review_id": rid,
            "sections": results
        }

    if st.session_state.review_results:
        results = st.session_state.review_results["sections"]

        # Build cards using same visual language as the app
        def _review_cards_html():
            cards = []
            for name in PAYLOAD_SECTION_NAMES:
                res = results.get(name, {})
                ts = html.escape(res.get("timestamp", ""))
                full = html.escape(res.get("llm_response", ""))
                snippet = html.escape(make_snippet(res.get("llm_response", "")))

                cards.append(
                    f'<div class="doc-chip">'
                    f'  <h5>{html.escape(name)}</h5>'
                    f'  <div class="doc-meta">Delivered • {ts}</div>'
                    f'  <div class="doc-meta">{snippet}</div>'
                    f'  <details class="card-details"><summary class="view-link">View full response</summary>'
                    f'    <div class="fulltext">{full}</div>'
                    f'  </details>'
                    f'  <span class="status-pill success">Ready</span>'
                    f'</div>'
                )
            return (
                '<div class="fanout-card">'
                '  <div class="fanout-title">Review Sections</div>'
                f'  <div class="doc-grid">{"".join(cards)}</div>'
                '</div>'
            )

        st.markdown(_review_cards_html(), unsafe_allow_html=True)

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

