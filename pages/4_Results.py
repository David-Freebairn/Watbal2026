"""
pages/4_Results.py
==================
Browse, compare and download all saved simulation results.
"""

import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

import streamlit as st
import pandas as pd

from core.styles import apply_styles
apply_styles()

ROOT        = Path(__file__).parent.parent
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)

MULTI_FILE  = RESULTS_DIR / "multi_scenario_results.json"

st.title("📁 Results browser")
st.caption("View, compare, and download all saved simulation outputs.")

# ── Multi-scenario summary table ──────────────────────────────────────────────
st.subheader("📊 Multi-scenario summary")

if MULTI_FILE.exists():
    try:
        saved = json.loads(MULTI_FILE.read_text())
        df_res = pd.DataFrame(saved)
        if not df_res.empty:
            # Group selector
            groups = ["All"] + sorted(df_res["group"].dropna().unique().tolist()) \
                     if "group" in df_res.columns else ["All"]
            g1, g2 = st.columns([3,1])
            with g1:
                sel_group = st.selectbox("Filter by group", groups)
            with g2:
                st.markdown("<br>", unsafe_allow_html=True)
                csv_all = df_res.to_csv(index=False).encode()
                st.download_button("⬇ All (CSV)", csv_all,
                                   "all_results.csv", "text/csv",
                                   use_container_width=True)
            if sel_group != "All":
                df_res = df_res[df_res["group"] == sel_group]

            # Display
            num_cols = [c for c in df_res.columns
                        if df_res[c].dtype in ("float64","int64")
                        and c not in ("start","end","nyears")]
            col_cfg  = {c: st.column_config.NumberColumn(format="%.1f")
                        for c in num_cols}

            st.dataframe(df_res, use_container_width=True,
                         hide_index=True, height=min(600,60+len(df_res)*38),
                         column_config=col_cfg)

            st.caption(f"{len(df_res)} scenario(s)  ·  "
                       f"Saved at `{MULTI_FILE}`")
        else:
            st.info("No multi-scenario results saved yet.")
    except Exception as e:
        st.error(f"Could not load results: {e}")
else:
    st.info("No multi-scenario results file found. Run some scenarios in the Multi-scenario page.")

# ── Individual run files ──────────────────────────────────────────────────────
st.divider()
st.subheader("📂 Individual run files")

annual_files  = sorted(RESULTS_DIR.glob("*_annual.csv"))
monthly_files = sorted(RESULTS_DIR.glob("*_monthly.csv"))
chart_files   = sorted(RESULTS_DIR.glob("*.png"))
json_files    = sorted(RESULTS_DIR.glob("*_summary.json"))

col1, col2 = st.columns(2)

with col1:
    st.markdown("**Annual CSVs**")
    if annual_files:
        sel_ann = st.selectbox("Select file", annual_files,
                               format_func=lambda f: f.name)
        df_ann = pd.read_csv(sel_ann, index_col=0)
        st.dataframe(df_ann.round(1), use_container_width=True, height=280)
        st.download_button("⬇ Download", sel_ann.read_bytes(),
                           sel_ann.name, "text/csv",
                           use_container_width=True)
    else:
        st.info("No annual CSV files yet.")

with col2:
    st.markdown("**Output charts (PNG)**")
    if chart_files:
        sel_png = st.selectbox("Select chart", chart_files,
                               format_func=lambda f: f.name)
        st.image(str(sel_png), use_container_width=True)
        st.download_button("⬇ Download", sel_png.read_bytes(),
                           sel_png.name, "image/png",
                           use_container_width=True)
    else:
        st.info("No chart files yet.")

# ── Run metadata ──────────────────────────────────────────────────────────────
if json_files:
    st.divider()
    st.subheader("📋 Run metadata")
    sel_json = st.selectbox("Select run", json_files,
                            format_func=lambda f: f.stem.replace("_summary",""))
    meta = json.loads(sel_json.read_text())
    st.json(meta)
