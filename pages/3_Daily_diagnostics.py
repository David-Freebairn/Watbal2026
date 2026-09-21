"""
pages/3_Daily_diagnostics.py
============================
Daily water balance diagnostics — cover, root depth, PAW, runoff and more.
Results cached in session state so changing chart variables
does not re-run the simulation.
"""

import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

st.set_page_config(page_title="Daily diagnostics", page_icon="📈", layout="wide")
st.markdown("""<style>
h1,h2,h3{white-space:nowrap!important;}
.block-container{padding-top:1rem;}
</style>""", unsafe_allow_html=True)

from core.styles import apply_styles, load_station
from core.silo import ensure_climate_cached, SiloUnavailableError, slice_climate
apply_styles()

ROOT = Path(__file__).parent.parent

def _find_dir(root, candidates):
    for name in candidates:
        d = Path(root) / name
        if d.exists(): return d
    try:
        for item in Path(root).iterdir():
            if item.is_dir() and item.name.lower() in [c.lower() for c in candidates]:
                return item
    except Exception: pass
    return Path(root) / candidates[0]

SOILS_DIR   = _find_dir(ROOT, ["Soils","Soil","soils"])
VEGE_DIR    = _find_dir(ROOT, ["Vegetation descriptions","Vegetation","Vege","vege"])
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)

def _scan(folder, exts):
    folder = Path(folder)
    if not folder.exists(): return []
    return sorted([f for f in folder.rglob("*") if f.suffix.lower() in exts])

soil_files = _scan(SOILS_DIR, {".soil",".prm",".xlsx",".xml"})
vege_files = _scan(VEGE_DIR,  {".vege",".xlsx"})

st.title("📈 Daily diagnostics")
st.caption("Daily water balance trace — cover, PAW, runoff, erosion and more. "
           "Changing chart variables does not re-run the model.")

# ── Climate from session ──────────────────────────────────────────────────────
clim_source = st.session_state.get("climate_source")
station     = load_station()
clim_label  = st.session_state.get("climate_label", "")
p51_info    = st.session_state.get("climate_p51_info", {})

if not clim_source:
    st.warning("No climate selected.")
    st.page_link("pages/1_Setup_scenarios.py", label="← Go to Setup scenarios to select climate")
    st.stop()

avail_start = int(p51_info.get("start","1900")[:4]) if clim_source == "local" else 1900
avail_end   = int(p51_info.get("end","2026")[:4])   if clim_source == "local" else 2026

# ── Config ────────────────────────────────────────────────────────────────────
with st.container(border=True):
    cc1, cc2 = st.columns([3, 2])
    with cc1:
        if clim_source == "silo" and station:
            st.markdown(f"**Climate:** 🌐 {station['name']}")
        else:
            st.markdown(f"**Climate:** 📂 {clim_label}  "
                        f"·  {p51_info.get('start','?')[:4]}–{p51_info.get('end','?')[:4]}")
        if st.button("← Change", key="chg"):
            st.switch_page("pages/1_Setup_scenarios.py")
    with cc2:
        dy1, dy2 = st.columns(2)
        with dy1:
            _def_s = st.session_state.get("_last_yr_start", max(1975, avail_start))
            yr_start = st.number_input("Start year", avail_start, avail_end,
                                        max(avail_start,min(avail_end,_def_s)), step=1)
        with dy2:
            _def_e = st.session_state.get("_last_yr_end", min(2000, avail_end))
            yr_end = st.number_input("End year", avail_start, avail_end,
                                      max(avail_start,min(avail_end,_def_e)), step=1)
        if yr_start >= yr_end:
            st.error("Start must be before end.")
            st.stop()

c1, c2 = st.columns(2)
with c1:
    if not soil_files:
        st.error("No soil files found."); st.stop()
    _s_idx = 0
    _last_soil = st.session_state.get("_last_soil_path","")
    for _i,_f in enumerate(soil_files):
        if str(_f) == _last_soil: _s_idx = _i; break
    soil_choice = st.selectbox("Soil", soil_files, index=_s_idx,
                               format_func=lambda f: f.stem)
    if soil_choice: st.session_state["_last_soil_path"] = str(soil_choice)
with c2:
    if not vege_files:
        st.error("No vegetation files found."); st.stop()
    _v_idx = 0
    _last_vege = st.session_state.get("_last_vege_path","")
    for _i,_f in enumerate(vege_files):
        if str(_f) == _last_vege: _v_idx = _i; break
    vege_choice = st.selectbox("Vegetation", vege_files, index=_v_idx,
                               format_func=lambda f: f.stem)
    if vege_choice: st.session_state["_last_vege_path"] = str(vege_choice)

run_btn = st.button("▶  Run", type="primary", use_container_width=True)

# Cache key — only re-run when inputs change
_run_key = f"{clim_source}|{clim_label}|{yr_start}|{yr_end}|{soil_choice}|{vege_choice}"
_have_cache = (st.session_state.get("_dc_key") == _run_key and
               st.session_state.get("_dc_df") is not None)

if not run_btn and not _have_cache:
    st.info("Configure above and click ▶ Run.")
    st.stop()

if run_btn or not _have_cache:
    with st.spinner("Running simulation…"):
        try:
            # Climate
            if clim_source == "silo":
                df_full = ensure_climate_cached(
                    station["id"], lat=station["lat"], lon=station["lon"],
                    session_state=st.session_state)
                met_df = slice_climate(df_full, f"{yr_start}0101", f"{yr_end}1231")
            else:
                from core.read_p51 import read_p51
                _, met_df = read_p51(Path(st.session_state["climate_p51_path"]))
                met_df = met_df[f"{yr_start}0101":f"{yr_end}1231"]

            if len(met_df) == 0:
                st.error(f"No data in range {yr_start}–{yr_end}."); st.stop()

            # Soil
            ext = soil_choice.suffix.lower()
            if ext in (".soil", ".xml"):
                from core.soil_xml import read_soil_xml
                profile = read_soil_xml(soil_choice)
            elif ext in (".xlsx", ".xls"):
                from core.soil_excel import read_soil_excel
                profile = read_soil_excel(soil_choice)
            else:
                from core.soil import read_prm
                profile = read_prm(soil_choice)

            # Vege
            ext = vege_choice.suffix.lower()
            if ext == ".vege":
                from core.vege import read_vege
                from core.run_simulation import _make_vege_fn
                get_state = _make_vege_fn(read_vege(vege_choice))
            else:
                from core.cover_excel import read_cover_excel
                from core.run_simulation import _make_cover_fn
                get_state = _make_cover_fn(read_cover_excel(vege_choice))

            # Run
            from core.run_simulation import _run_daily, _annual_stats
            df, sw0, swf = _run_daily(met_df, profile, get_state)
            nyears = met_df.index.year.nunique()
            ann    = _annual_stats(df)
            dsw    = swf - sw0
            err    = (df.rain.sum() - df.runoff.sum() - df.drainage.sum()
                      - df.soil_evap.sum() - df.transp.sum() - dsw) / nyears

            # Cache
            st.session_state["_dc_key"]      = _run_key
            st.session_state["_dc_df"]       = df
            st.session_state["_dc_profile"]  = profile
            st.session_state["_dc_nyears"]   = nyears
            st.session_state["_dc_err"]      = err
            st.session_state["_dc_ann"]      = ann
            st.session_state["_dc_simdate"]  = datetime.now().strftime("%Y%m%d")

        except Exception as e:
            st.error(f"Simulation failed: {e}")
            st.stop()

# ── Retrieve from cache ───────────────────────────────────────────────────────
df       = st.session_state["_dc_df"]
profile  = st.session_state["_dc_profile"]
nyears   = st.session_state["_dc_nyears"]
err      = st.session_state["_dc_err"]
ann      = st.session_state["_dc_ann"]
_sim_date = st.session_state.get("_dc_simdate", datetime.now().strftime("%Y%m%d"))

st.success(f"✅ {nyears} years  ·  Balance error = {err:+.5f} mm/yr")

# ── Build display dataframe ───────────────────────────────────────────────────
col_map = {
    "rain":          "Rain (mm)",
    "runoff":        "Runoff (mm)",
    "soil_evap":     "Soil evap (mm)",
    "transp":        "Transp (mm)",
    "drainage":      "Drainage (mm)",
    "sw_total":      "SW total (mm)",
    "pasw":          "PAW (mm)",
    "green_cover":   "Green cover %",
    "residue_cover": "Residue cover %",
    "root_depth":    "Root depth (mm)",
    "cn2_eff":       "CN (cover+soil water)",
    "cn2_cover":     "CN after cover",
    "S_value":       "S retention (mm)",
    "sumh20":        "sumh20 (AMC)",
}
if "sediment" in df.columns: col_map["sediment"] = "Erosion (t/ha)"
if "yield"    in df.columns: col_map["yield"]     = "Yield (t/ha)"

daily = df[[c for c in col_map if c in df.columns]].rename(columns=col_map)
daily.index.name = "Date"

# ── Date range filter ─────────────────────────────────────────────────────────
st.divider()
years = sorted(df.index.year.unique())
fc1, fc2, fc3 = st.columns([2, 2, 3])
with fc1:
    view_start = st.selectbox("From year", years, index=0, key="vs")
with fc2:
    view_end = st.selectbox("To year", years, index=len(years)-1, key="ve")
with fc3:
    sel_years = st.multiselect("Or pick specific years", years, default=[], key="sy")

if sel_years:
    daily_show = daily[daily.index.year.isin(sel_years)]
elif view_start <= view_end:
    daily_show = daily[str(view_start):str(view_end)]
else:
    daily_show = daily

st.caption(f"Showing {len(daily_show):,} days  ·  "
           f"Drag or use 1m/3m/6m/1y buttons to zoom  ·  Double-click to reset zoom")

# ── Chart ─────────────────────────────────────────────────────────────────────
st.subheader("📊 Daily trace")

all_vars = list(col_map.values())

# Updated default variables
_default_vars = [v for v in [
    "Rain (mm)", "Runoff (mm)", "Green cover %",
    "Residue cover %", "PAW (mm)", "Erosion (t/ha)"
] if v in all_vars]

chart_vars = st.multiselect(
    "Variables to plot",
    all_vars,
    default=_default_vars,
    key="cv",
)

SECONDARY = {"Green cover %","Residue cover %","Root depth (mm)","PAW (mm)",
             "Erosion (t/ha)","Yield (t/ha)","CN (cover+soil water)","CN after cover",
             "sumh20 (AMC)"}
COLOURS = {
    "Rain (mm)":              "rgba(90,150,212,0.5)",
    "Runoff (mm)":            "#2C4A7A",
    "Soil evap (mm)":         "#C8402A",
    "Transp (mm)":            "#4A8A3A",
    "Drainage (mm)":          "#C48A18",
    "SW total (mm)":          "#6A3A9A",
    "PAW (mm)":               "#9A6ACA",
    "Green cover %":          "#2E7D32",
    "Residue cover %":        "#8D6E63",
    "Root depth (mm)":        "#E65100",
    "Erosion (t/ha)":         "#333333",
    "Yield (t/ha)":           "#F57F17",
    "CN (cover+soil water)":  "#7B1FA2",
    "CN after cover":         "#AB47BC",
    "S retention (mm)":       "#4527A0",
    "sumh20 (AMC)":           "#0277BD",
}

if chart_vars:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    for v in chart_vars:
        if v not in daily_show.columns: continue
        is_sec = v in SECONDARY
        col    = COLOURS.get(v, "#888")
        if v == "Rain (mm)":
            fig.add_trace(go.Bar(x=daily_show.index, y=daily_show[v],
                                  name=v, marker_color=col, opacity=0.6),
                          secondary_y=False)
        else:
            fig.add_trace(go.Scatter(x=daily_show.index, y=daily_show[v],
                                      name=v, line=dict(color=col, width=1.5),
                                      mode="lines"),
                          secondary_y=is_sec)

    # LL and DUL lines when SW total shown
    if "SW total (mm)" in chart_vars:
        ll  = sum(l.ll_mm  for l in profile.layers)
        dul = sum(l.dul_mm for l in profile.layers)
        for val, lbl, col in [(ll, f"LL {ll:.0f}mm", "#c0392b"),
                               (dul, f"DUL {dul:.0f}mm", "#2980b9")]:
            fig.add_hline(y=val, line=dict(color=col, width=1, dash="dash"),
                          annotation_text=lbl, annotation_position="right",
                          secondary_y=False)

    # PAW capacity line
    if "PAW (mm)" in chart_vars:
        pawc = sum(getattr(l, 'pawc', getattr(l, 'dul_mm', 0) - getattr(l, 'll_mm', 0))
                   for l in profile.layers)
        fig.add_hline(y=pawc, line=dict(color="#9A6ACA", width=1, dash="dot"),
                      annotation_text=f"PAWC {pawc:.0f}mm",
                      annotation_position="right", secondary_y=True)

    fig.update_layout(
        height=500,
        margin=dict(l=55, r=70, t=20, b=30),
        legend=dict(orientation="h", x=0.5, xanchor="center", y=-0.08,
                    font=dict(size=11)),
        plot_bgcolor="white", paper_bgcolor="white",
        hovermode="x unified", bargap=0,
        xaxis=dict(
            showgrid=False, tickfont=dict(size=11),
            rangeslider=dict(visible=True, thickness=0.05),
            rangeselector=dict(
                buttons=[
                    dict(count=1,  label="1m", step="month", stepmode="backward"),
                    dict(count=3,  label="3m", step="month", stepmode="backward"),
                    dict(count=6,  label="6m", step="month", stepmode="backward"),
                    dict(count=1,  label="1y", step="year",  stepmode="backward"),
                    dict(step="all", label="All"),
                ],
                font=dict(size=11),
            ),
        ),
        yaxis=dict(title="mm", rangemode="tozero",
                   gridcolor="rgba(0,0,0,0.06)", tickfont=dict(size=11)),
        yaxis2=dict(title="Cover % / Root mm / Erosion",
                    showgrid=False, tickfont=dict(size=11)),
    )

    # Chart + PNG export button
    ch1, ch2 = st.columns([6, 1])
    with ch1:
        st.plotly_chart(fig, use_container_width=True)
    with ch2:
        try:
            _safe_stem = (f"{clim_label}_{soil_choice.stem}_{vege_choice.stem}"
                          f"_{yr_start}_{yr_end}_{_sim_date}").replace(" ","_")[:80]
            _png = fig.to_image(format="png", width=1400, height=500)
            st.download_button("⬇ PNG", _png, f"{_safe_stem}_daily.png",
                               "image/png", use_container_width=True)
        except Exception:
            pass  # kaleido not installed

# ── Annual summary ────────────────────────────────────────────────────────────
st.divider()
st.subheader("Annual summary")
rain_m = float(ann["rain"].mean())
rows = []
for k, label in [("rain","Rainfall"),("runoff","Runoff"),("soil_evap","Soil evap"),
                  ("transp","Transpiration"),("et","Total ET"),("drainage","Drainage")]:
    v = float(ann[k].mean())
    rows.append({"Component": label,
                 "Mean mm/yr": int(round(v)),
                 "% rain": f"{v/rain_m*100:.0f}",
                 "CV%": f"{ann[k].std()/max(ann[k].mean(),0.1)*100:.0f}",
                 "Min": int(round(float(ann[k].min()))),
                 "Max": int(round(float(ann[k].max())))})
if "sediment" in ann.columns:
    sv = float(ann["sediment"].mean())
    rows.append({"Component":"Erosion (t/ha)","Mean mm/yr":round(sv,2),"% rain":"—",
                 "CV%":f"{ann['sediment'].std()/max(sv,0.01)*100:.0f}",
                 "Min":round(float(ann['sediment'].min()),2),
                 "Max":round(float(ann['sediment'].max()),2)})
yd = df.attrs.get("annual_yield",{})
if yd:
    yv = [v for v in yd.values() if v > 0]
    if yv:
        rows.append({"Component":"Yield (t/ha)","Mean mm/yr":round(np.mean(yv),2),"% rain":"—",
                     "CV%":f"{np.std(yv)/max(np.mean(yv),0.01)*100:.0f}",
                     "Min":round(float(np.min(yv)),2),"Max":round(float(np.max(yv)),2)})

df_ann = pd.DataFrame(rows)
st.dataframe(df_ann, use_container_width=True, hide_index=True)
if abs(err) < 0.001:
    st.caption(f"Balance error = {err:+.5f} mm/yr  ✅")
else:
    st.warning(f"Balance error = {err:+.5f} mm/yr  ⚠️")

# ── Downloads ─────────────────────────────────────────────────────────────────
st.divider()
safe = (f"{clim_label}_{soil_choice.stem}_{vege_choice.stem}"
        f"_{yr_start}_{yr_end}_{_sim_date}").replace(" ","_")[:80]
d1, d2, d3 = st.columns(3)
with d1:
    st.download_button("⬇ Daily CSV (all years)", daily.to_csv().encode(),
                       f"{safe}_daily.csv", "text/csv", use_container_width=True)
with d2:
    if len(daily_show) < len(daily):
        st.download_button("⬇ Daily CSV (filtered view)", daily_show.to_csv().encode(),
                           f"{safe}_daily_filtered.csv", "text/csv", use_container_width=True)
with d3:
    st.download_button("⬇ Annual summary CSV", df_ann.to_csv(index=False).encode(),
                       f"{safe}_annual_summary.csv", "text/csv", use_container_width=True)

daily.to_csv(RESULTS_DIR / f"{safe}_daily.csv")
st.caption(f"💾 Saved to `results/{safe}_daily.csv`")
