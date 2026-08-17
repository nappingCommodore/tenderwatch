"""Editorial UI helpers: injects CSS to de-Streamlit the look and renders the
BidWatchDog-style masthead, section headers, big stats and ranked bars."""

from __future__ import annotations

import html

import streamlit as st

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;700;800&family=Fraunces:opsz,wght@9..144,600;9..144,800&display=swap');

#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {display:none !important;}
[data-testid="stHeader"] {background:transparent; height:0;}
.block-container {padding-top:2rem; padding-bottom:4rem; max-width:1180px;}
html, body, [class*="css"] {font-family:'Inter',system-ui,sans-serif;}
h1,h2,h3 {font-family:'Fraunces',Georgia,serif !important; letter-spacing:-0.015em;}

.mast {border-top:3px solid #e0533d; padding-top:0.6rem; margin-bottom:0.2rem;}
.mast .kick {color:#e0533d; font-weight:800; font-size:0.72rem; letter-spacing:0.22em; text-transform:uppercase;}
.mast h1 {font-size:2.5rem; margin:0.1rem 0 0.2rem 0; font-weight:800;}
.mast .sub {color:#9aa0a8; font-size:0.95rem; max-width:720px;}

.kpis {display:flex; gap:2.2rem; flex-wrap:wrap; margin:1.1rem 0 0.4rem 0;
       border-top:1px solid #262a33; border-bottom:1px solid #262a33; padding:0.9rem 0;}
.kpi .n {font-family:'Fraunces',serif; font-size:1.9rem; font-weight:800; line-height:1;}
.kpi .l {color:#8a8f98; font-size:0.72rem; letter-spacing:0.06em; text-transform:uppercase; margin-top:0.25rem;}

.sec {margin-top:2.4rem;}
.sec .num {color:#e0533d; font-weight:800; font-size:0.72rem; letter-spacing:0.18em;}
.sec h2 {font-size:1.55rem; margin:0.15rem 0 0.15rem 0;}
.sec .cap {color:#9aa0a8; font-size:0.9rem; max-width:760px; margin-bottom:0.7rem;}

.rowbar {position:relative; background:#161922; border:1px solid #20242e; border-radius:5px;
         margin:4px 0; padding:8px 12px; overflow:hidden; font-size:0.9rem;}
.rowbar .fill {position:absolute; left:0; top:0; bottom:0; background:rgba(224,83,61,0.16); border-right:2px solid rgba(224,83,61,0.5);}
.rowbar .lbl {position:relative; z-index:1; color:#e8e6e3;}
.rowbar .val {position:relative; z-index:1; float:right; color:#c9cdd4; font-weight:700; font-variant-numeric:tabular-nums;}
.note {color:#b8bcc4; font-size:0.86rem; font-style:italic; border-left:2px solid #e0533d;
       padding:0.15rem 0 0.15rem 0.8rem; margin:0.7rem 0 0.2rem 0; max-width:760px;}
.disc {color:#6b7079; font-size:0.8rem; border-top:1px solid #262a33; margin-top:3rem; padding-top:1rem;}
</style>
"""


def inject() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def masthead(kick: str, title: str, subtitle: str, kpis: list[tuple[str, str]]) -> None:
    cells = "".join(
        f"<div class='kpi'><div class='n'>{html.escape(v)}</div>"
        f"<div class='l'>{html.escape(l)}</div></div>"
        for v, l in kpis
    )
    st.markdown(
        f"<div class='mast'><div class='kick'>{html.escape(kick)}</div>"
        f"<h1>{html.escape(title)}</h1><div class='sub'>{html.escape(subtitle)}</div>"
        f"<div class='kpis'>{cells}</div></div>",
        unsafe_allow_html=True,
    )


def section(num: str, title: str, caption: str = "") -> None:
    cap = f"<div class='cap'>{html.escape(caption)}</div>" if caption else ""
    st.markdown(
        f"<div class='sec'><div class='num'>{html.escape(num)}</div>"
        f"<h2>{html.escape(title)}</h2>{cap}</div>",
        unsafe_allow_html=True,
    )


def bars(rows: list[tuple[str, str, float]]) -> None:
    """rows = list of (label, value_text, fraction 0..1)."""
    out = []
    for label, val, frac in rows:
        frac = max(0.0, min(1.0, float(frac or 0)))
        out.append(
            f"<div class='rowbar'><div class='fill' style='width:{frac*100:.1f}%'></div>"
            f"<span class='lbl'>{html.escape(str(label))}</span>"
            f"<span class='val'>{html.escape(str(val))}</span></div>"
        )
    st.markdown("".join(out), unsafe_allow_html=True)


def note(text: str) -> None:
    """Short editorial takeaway rendered under a section's bars."""
    st.markdown(f"<div class='note'>{html.escape(text)}</div>", unsafe_allow_html=True)


def disclaimer(text: str) -> None:
    st.markdown(f"<div class='disc'>{html.escape(text)}</div>", unsafe_allow_html=True)
