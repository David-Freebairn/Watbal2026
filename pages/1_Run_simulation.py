"""
pages/1_Run_simulation.py
=========================
Unified simulation flow:
  Step 1 — Select climate (SILO or local P51)
  Step 2 — Confirm climate + set date range
  Step 3 — Single or multiple simulation
            Single:   1 soil × 1 vege  → run → monthly chart
            Multiple: N soils × M vege → run all → summary table
                      click any row → view individual monthly chart
"""

import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

import streamlit as st
import pandas as pd
import numpy as np

from core.styles import apply_styles, load_station, set_station
from core.nav import HOME
from core.silo import (ensure_climate_cached, SiloUnavailableError,
                        slice_climate, search_stations)
from core.reliability import reliability_color, reliability_label, _load as _load_rel

apply_styles()

# Resolve ROOT robustly — works whether Streamlit runs from repo root or pages/
_here = Path(__file__).resolve()          # .../pages/1_Run_simulation.py
ROOT  = _here.parent.parent               # .../Watbal2026/
# Sanity check — if Soils/core not found, try cwd
if not (ROOT / 'core').exists() and (Path.cwd() / 'core').exists():
    ROOT = Path.cwd()
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)
MULTI_FILE  = RESULTS_DIR / "multi_scenario_results.json"

def _find_dir(root, candidates):
    """Find a subfolder by trying several name variants (case-insensitive)."""
    root = Path(root)
    # Try exact names first
    for name in candidates:
        d = root / name
        if d.exists():
            return d
    # Case-insensitive fallback
    try:
        for item in root.iterdir():
            if item.is_dir():
                low = item.name.lower()
                for name in candidates:
                    if low == name.lower():
                        return item
    except Exception:
        pass
    return root / candidates[0]   # return default even if missing

SOILS_DIR = _find_dir(ROOT, ["Soils", "Soil", "soils", "soil"])
VEGE_DIR  = _find_dir(ROOT, ["Vegetation descriptions", "Vegetation",
                              "Vege", "vege", "vegetation descriptions",
                              "vegetation"])
CLIM_DIR  = _find_dir(ROOT, ["Climate files", "Climate", "climate files",
                              "climate", "Data"])

def _scan(folder, exts):
    folder = Path(folder)
    if not folder.exists():
        return []
    return sorted([f for f in folder.rglob("*") if f.suffix.lower() in exts])

soil_files = _scan(SOILS_DIR, {".soil", ".prm", ".xlsx", ".xml"})
vege_files = _scan(VEGE_DIR,  {".vege", ".xlsx"})
p51_files  = _scan(CLIM_DIR,  {".p51"})

# Debug — log what was found (shows in terminal)
import logging as _log
_log.getLogger().setLevel(_log.WARNING)
print(f"[Watbal] ROOT={ROOT}")
print(f"[Watbal] SOILS_DIR={SOILS_DIR}  exists={SOILS_DIR.exists()}  files={len(soil_files)}")
print(f"[Watbal] VEGE_DIR={VEGE_DIR}  exists={VEGE_DIR.exists()}  files={len(vege_files)}")

def _load_saved() -> list:
    if MULTI_FILE.exists():
        try:
            return json.loads(MULTI_FILE.read_text())
        except Exception:
            return []
    return []

def _save_results(results: list):
    MULTI_FILE.write_text(json.dumps(results, indent=2, default=str))

# ─────────────────────────────────────────────────────────────────────────────
st.title("⚙️ Run simulation")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Climate (read from session state, set on Home page)
# ══════════════════════════════════════════════════════════════════════════════
clim_source = st.session_state.get("climate_source")   # "silo" or "local"
station     = load_station()                            # set on Home page

with st.container(border=True):
    st.subheader("Step 1 — Climate")

    if clim_source == "silo" and station:
        rel_df = _load_rel()
        pct = None
        if rel_df is not None:
            try:
                pct = float(rel_df.loc[int(station["id"]), "pct_observed"])
            except Exception:
                pass
        st.success(
            f"🌐 **{station['name']}**  [{station.get('state','')}]  "
            f"({station['lat']:.3f}, {station['lon']:.3f})  ·  "
            f"{reliability_label(pct)}"
        )
        if st.button("← Change climate source", key="change_clim"):
            st.switch_page("app.py")

    elif clim_source == "local":
        p51_path  = st.session_state.get("climate_p51_path")
        p51_info  = st.session_state.get("climate_p51_info", {})
        clim_label_ss = st.session_state.get("climate_label", "Local P51")
        if p51_path:
            st.success(
                f"📂 **{clim_label_ss}**  ·  "
                f"{p51_info.get('start','?')} → {p51_info.get('end','?')}  "
                f"·  {p51_info.get('years','?')} years  "
                f"·  ~{p51_info.get('rain','?'):.0f} mm/yr"
            )
        if st.button("← Change climate source", key="change_clim"):
            st.switch_page("app.py")

    else:
        st.warning("No climate source selected.")
        st.page_link("app.py", label="← Go to Home to select a climate source")
        st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Date range (only shown once climate is selected)
# ══════════════════════════════════════════════════════════════════════════════
if clim_source is None:
    st.info("Select a climate station or P51 file above to continue.")
    st.stop()

with st.container(border=True):
    st.subheader("Step 2 — Simulation period")

    # Determine available date bounds
    if clim_source == "local":
        p51_info    = st.session_state.get("climate_p51_info", {})
        avail_start = int(p51_info.get("start","1900")[:4])
        avail_end   = int(p51_info.get("end","2026")[:4])
        st.caption(f"P51 file covers {avail_start}–{avail_end}")
    else:
        avail_start = 1900
        avail_end   = 2026

    c1, c2 = st.columns(2)
    with c1:
        yr_start = st.number_input(
            "Start year", avail_start, avail_end,
            max(1975, avail_start), step=1, key="yr_start",
        )
    with c2:
        yr_end = st.number_input(
            "End year", avail_start, avail_end,
            min(2000, avail_end), step=1, key="yr_end",
        )

    if yr_start >= yr_end:
        st.error("Start year must be before end year.")
        st.stop()

    st.caption(f"→ {yr_end - yr_start + 1} years  ({yr_start}–{yr_end})")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Soil, vegetation, single vs multiple
# ══════════════════════════════════════════════════════════════════════════════
with st.container(border=True):
    st.subheader("Step 3 — Soil & vegetation")

    if not soil_files:
        st.error(f"No soil files found in `{SOILS_DIR.relative_to(ROOT)}/`")
        st.stop()
    if not vege_files:
        st.error(f"No vegetation files found in `{VEGE_DIR.relative_to(ROOT)}/`")
        st.stop()

    sim_mode = st.radio(
        "Simulation mode",
        ["Single  (1 soil × 1 vegetation)", "Multiple  (1+ soils × 1+ vegetation)"],
        horizontal=True,
        key="sim_mode",
    )
    multiple = sim_mode.startswith("Multiple")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Soil(s)**")
        if multiple:
            soil_sel = st.multiselect(
                "Soils", soil_files, format_func=lambda f: f.stem,
                key="soil_multi",
            )
        else:
            s = st.selectbox("Soil", soil_files, format_func=lambda f: f.stem,
                             key="soil_single")
            soil_sel = [s] if s else []

    with c2:
        st.markdown("**Vegetation**")
        if multiple:
            vege_sel = st.multiselect(
                "Vegetation", vege_files, format_func=lambda f: f.stem,
                key="vege_multi",
            )
        else:
            v = st.selectbox("Vegetation", vege_files, format_func=lambda f: f.stem,
                             key="vege_single")
            vege_sel = [v] if v else []

    n_combos = len(soil_sel) * len(vege_sel)
    if multiple and n_combos > 0:
        st.info(f"→ **{n_combos} simulation(s)**  "
                f"({len(soil_sel)} soil{'s' if len(soil_sel)>1 else ''} × "
                f"{len(vege_sel)} vegetation)")

    if multiple:
        _clim_prefix = (station['name'].split()[0] if clim_source == 'silo'
                        else st.session_state.get('climate_label', 'P51').split()[0])
        group_name = st.text_input(
            "Group name (for results table)",
            value=f"{_clim_prefix}_{yr_start}_{yr_end}",
            key="group_name",
        )

# ══════════════════════════════════════════════════════════════════════════════
# RUN BUTTON
# ══════════════════════════════════════════════════════════════════════════════
if not soil_sel or not vege_sel:
    st.warning("Select at least one soil and one vegetation file to run.")
    st.stop()

run_label = f"▶  Run {n_combos} simulation(s)" if multiple else "▶  Run simulation"
run_btn = st.button(run_label, type="primary", width="stretch")

if not run_btn:
    st.stop()

# ── Load climate ──────────────────────────────────────────────────────────────
with st.spinner("Loading climate data…"):
    try:
        if clim_source == "silo":
            df_full = ensure_climate_cached(
                station["id"], lat=station["lat"], lon=station["lon"],
                session_state=st.session_state,
            )
            met_df     = slice_climate(df_full, f"{yr_start}0101", f"{yr_end}1231")
            clim_label = station["name"]
        else:
            from core.read_p51 import read_p51
            p51_path   = st.session_state.get("climate_p51_path")
            _, met_df  = read_p51(Path(p51_path))
            met_df     = met_df[f"{yr_start}0101":f"{yr_end}1231"]
            clim_label = st.session_state.get("climate_label", Path(p51_path).stem)
    except SiloUnavailableError as e:
        st.error(f"SILO unavailable: {e}")
        st.info("Try selecting a local P51 file instead.")
        st.stop()
    except Exception as e:
        st.error(f"Climate load failed: {e}")
        st.stop()

if len(met_df) == 0:
    st.error(f"No climate data in range {yr_start}–{yr_end}. "
             f"Check the file covers this period.")
    st.stop()

nyears = met_df.index.year.nunique()

# ── Helper: load soil profile ─────────────────────────────────────────────────
def _load_profile(soil_path):
    ext = soil_path.suffix.lower()
    if ext in (".soil", ".xml"):
        from core.soil_xml import read_soil_xml
        return read_soil_xml(soil_path)
    elif ext in (".xlsx", ".xls"):
        from core.soil_excel import read_soil_excel
        return read_soil_excel(soil_path)
    else:
        from core.soil import read_prm
        return read_prm(soil_path)

# ── Helper: load vege function ────────────────────────────────────────────────
def _load_vege(vege_path):
    ext = vege_path.suffix.lower()
    if ext == ".vege":
        from core.vege import read_vege
        from core.run_simulation import _make_vege_fn
        return _make_vege_fn(read_vege(vege_path))
    else:
        from core.cover_excel import read_cover_excel
        from core.run_simulation import _make_cover_fn
        return _make_cover_fn(read_cover_excel(vege_path))

# ── Helper: run one simulation ────────────────────────────────────────────────
def _run_one(met_df, profile, get_state, nyears):
    from core.run_simulation import _run_daily, _monthly_means, _annual_stats
    df_out, sw0, swf = _run_daily(met_df, profile, get_state)
    dsw = swf - sw0
    err = (df_out.rain.sum() - df_out.runoff.sum() - df_out.drainage.sum()
           - df_out.soil_evap.sum() - df_out.transp.sum() - dsw) / nyears
    ann = _annual_stats(df_out)
    mon = _monthly_means(df_out, nyears)
    ann.attrs["annual_yield"] = df_out.attrs.get("annual_yield", {})
    return ann, mon, err, df_out

# ── Monthly chart helper ──────────────────────────────────────────────────────
def _monthly_chart(mon, ann, clim_label, soil_name, vege_name, yr_start, yr_end):
    import plotly.graph_objects as go
    MO = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=MO, y=mon["rain"].round(1).tolist(),
        name="Rainfall", marker_color="rgba(90,150,212,0.55)", yaxis="y",
    ))
    fig.add_trace(go.Scatter(
        x=MO, y=mon["soil_evap"].round(1).tolist(), name="Soil Evap.",
        line=dict(color="#C8402A", width=2.5), mode="lines+markers",
        marker=dict(size=6), yaxis="y",
    ))
    fig.add_trace(go.Scatter(
        x=MO, y=mon["transp"].round(1).tolist(), name="Transpiration",
        line=dict(color="#4A8A3A", width=2.5), mode="lines+markers",
        marker=dict(size=6), yaxis="y",
    ))
    fig.add_trace(go.Scatter(
        x=MO, y=mon["runoff"].round(1).tolist(), name="Runoff",
        line=dict(color="#2C4A7A", width=2.0), mode="lines+markers",
        marker=dict(size=5), yaxis="y",
    ))
    fig.add_trace(go.Scatter(
        x=MO, y=mon["drainage"].round(1).tolist(), name="Drainage",
        line=dict(color="#C48A18", width=2.0), mode="lines+markers",
        marker=dict(size=5), yaxis="y",
    ))
    if "sediment" in mon.columns:
        fig.add_trace(go.Scatter(
            x=MO, y=mon["sediment"].round(2).tolist(), name="Erosion (t/ha)",
            line=dict(color="#333", width=2, dash="dash"),
            mode="lines+markers", marker=dict(size=5, symbol="diamond"),
            yaxis="y2",
        ))
    rm = float(ann["rain"].mean())
    fig.update_layout(
        title=dict(
            text=(f"Water balance summary  {yr_start}–{yr_end}  ·  "
                  f"Rain {rm:.0f}mm  "
                  f"Runoff {ann['runoff'].mean():.0f}mm  "
                  f"Evap {ann['soil_evap'].mean():.0f}mm  "
                  f"Transp {ann['transp'].mean():.0f}mm  "
                  f"Drain {ann['drainage'].mean():.0f}mm"),
            x=0.5, xanchor="center", font=dict(size=13),
        ),
        height=420, margin=dict(l=55, r=60, t=60, b=90),
        legend=dict(orientation="h", x=0.5, xanchor="center", y=-0.25,
                    font=dict(size=11)),
        plot_bgcolor="white", paper_bgcolor="white",
        hovermode="x unified", bargap=0.25,
        yaxis=dict(title="mm / month", rangemode="tozero",
                   gridcolor="rgba(0,0,0,0.06)", tickfont=dict(size=11)),
        yaxis2=dict(title="Erosion (t/ha/month)", overlaying="y", side="right",
                    rangemode="tozero", showgrid=False, tickfont=dict(size=11)),
        xaxis=dict(showgrid=False, tickfont=dict(size=12)),
    )
    # Config labels below chart
    st.plotly_chart(fig, width="stretch")
    cc1, cc2, cc3 = st.columns(3)
    cc1.caption(f"🌍 **Climate:** {clim_label}")
    cc2.caption(f"🪨 **Soil:** {soil_name}")
    cc3.caption(f"🌿 **Vegetation:** {vege_name}")

# ── Annual summary table helper ───────────────────────────────────────────────
def _ann_table(ann):
    rain_m = float(ann["rain"].mean())
    rows = []
    for k, label in [("rain","Rainfall"),("runoff","Runoff"),
                     ("soil_evap","Soil evaporation"),("transp","Transpiration"),
                     ("et","Total ET"),("drainage","Deep drainage")]:
        v  = float(ann[k].mean())
        cv = float(ann[k].std() / max(ann[k].mean(), 0.1) * 100)
        rows.append({"Component": label,
                     "Mean mm/yr": round(v,1),
                     "% rain": f"{v/rain_m*100:.1f}%",
                     "CV%": f"{cv:.0f}%",
                     "Min": round(float(ann[k].min()),1),
                     "Max": round(float(ann[k].max()),1)})
    if "sediment" in ann.columns:
        sv = float(ann["sediment"].mean())
        rows.append({"Component": "Erosion (t/ha)",
                     "Mean mm/yr": round(sv,2), "% rain":"—",
                     "CV%": f"{float(ann['sediment'].std()/max(sv,0.01)*100):.0f}%",
                     "Min": round(float(ann["sediment"].min()),2),
                     "Max": round(float(ann["sediment"].max()),2)})
    yd = ann.attrs.get("annual_yield",{})
    if yd:
        yv = list(yd.values())
        rows.append({"Component": "Yield (t/ha)",
                     "Mean mm/yr": round(float(np.mean(yv)),2), "% rain":"—",
                     "CV%": f"{float(np.std(yv)/max(np.mean(yv),0.01)*100):.0f}%",
                     "Min": round(float(np.min(yv)),2),
                     "Max": round(float(np.max(yv)),2)})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# SINGLE SIMULATION
# ══════════════════════════════════════════════════════════════════════════════
if not multiple:
    soil_path = soil_sel[0]
    vege_path = vege_sel[0]

    with st.spinner("Running simulation…"):
        try:
            profile   = _load_profile(soil_path)
            get_state = _load_vege(vege_path)
            ann, mon, err, df_out = _run_one(met_df, profile, get_state, nyears)
        except Exception as e:
            st.error(f"Simulation failed: {e}")
            st.stop()

    dsw = 0.0  # already accounted in err
    st.success(f"✅ Done — {nyears} years  |  Balance error = {err:+.4f} mm/yr")

    # Monthly chart
    _monthly_chart(mon, ann, clim_label, profile.name,
                   vege_path.stem, yr_start, yr_end)

    # Annual summary table
    st.subheader("Annual water balance summary")
    _ann_table(ann)

    # Save annual CSV
    safe = f"{clim_label}_{profile.name}_{vege_path.stem}_{yr_start}_{yr_end}".replace(" ","_")[:60]
    ann_out = ann[["rain","runoff","drainage","soil_evap","transp","et"]].copy()
    ann_out.columns = ["rain_mm","runoff_mm","drainage_mm","soil_evap_mm","transp_mm","et_mm"]
    if "sediment" in ann.columns:
        ann_out["erosion_t_ha"] = ann["sediment"].values
    yd = df_out.attrs.get("annual_yield",{})
    if yd:
        ann_out["yield_t_ha"] = ann_out.index.map(lambda y: round(yd.get(y,0.0),3))
    ann_out.to_csv(RESULTS_DIR / f"{safe}_annual.csv")

    # Downloads
    st.divider()
    d1, d2 = st.columns(2)
    with d1:
        st.download_button("⬇ Annual CSV",
                           ann_out.to_csv().encode(),
                           f"{safe}_annual.csv", "text/csv",
                           width="stretch")
    with d2:
        st.download_button("⬇ Monthly CSV",
                           mon.to_csv().encode(),
                           f"{safe}_monthly.csv", "text/csv",
                           width="stretch")

# ══════════════════════════════════════════════════════════════════════════════
# MULTIPLE SIMULATIONS
# ══════════════════════════════════════════════════════════════════════════════
else:
    total     = len(soil_sel) * len(vege_sel)
    prog      = st.progress(0, text="Running scenarios…")
    done      = 0
    new_rows  = []
    # Store (mon, ann) for each combo for individual drill-down
    run_store = {}

    for soil_path in soil_sel:
        try:
            profile = _load_profile(soil_path)
        except Exception as e:
            st.warning(f"Skipping soil {soil_path.stem}: {e}")
            done += len(vege_sel)
            continue

        for vege_path in vege_sel:
            done += 1
            prog.progress(
                done / total,
                text=f"Sim {done}/{total}: {soil_path.stem} × {vege_path.stem}",
            )
            try:
                get_state = _load_vege(vege_path)
                ann, mon, err, df_out = _run_one(met_df, profile, get_state, nyears)

                row = {
                    "group"     : group_name,
                    "climate"   : clim_label,
                    "soil"      : profile.name,
                    "vege"      : vege_path.stem,
                    "start"     : yr_start,
                    "end"       : yr_end,
                    "nyears"    : nyears,
                    "rainfall"  : round(float(ann["rain"].mean()),    1),
                    "irrigation": 0.0,
                    "soil_evap" : round(float(ann["soil_evap"].mean()),1),
                    "transp"    : round(float(ann["transp"].mean()),   1),
                    "et"        : round(float(ann["et"].mean()),       1),
                    "runoff"    : round(float(ann["runoff"].mean()),   1),
                    "drainage"  : round(float(ann["drainage"].mean()), 1),
                    "err_yr"    : round(err, 4),
                }
                if "sediment" in ann.columns:
                    row["erosion_t_ha"] = round(float(ann["sediment"].mean()), 2)
                yd = df_out.attrs.get("annual_yield", {})
                if yd:
                    row["yield_t_ha"] = round(sum(yd.values())/len(yd), 2)

                key = f"{profile.name}||{vege_path.stem}"
                run_store[key] = {"ann": ann, "mon": mon,
                                  "soil": profile.name, "vege": vege_path.stem}
                new_rows.append(row)

                # Save CSV
                safe = (f"{clim_label}_{profile.name}_{vege_path.stem}"
                        f"_{yr_start}_{yr_end}").replace(" ","_")[:60]
                ann_out = ann[["rain","runoff","drainage","soil_evap","transp","et"]].copy()
                ann_out.columns = ["rain_mm","runoff_mm","drainage_mm",
                                   "soil_evap_mm","transp_mm","et_mm"]
                if "sediment" in ann.columns:
                    ann_out["erosion_t_ha"] = ann["sediment"].values
                if yd:
                    ann_out["yield_t_ha"] = ann_out.index.map(
                        lambda y: round(yd.get(y,0.0),3))
                ann_out.to_csv(RESULTS_DIR / f"{safe}_annual.csv")

            except Exception as e:
                st.warning(f"Sim {done} failed ({soil_path.stem} × {vege_path.stem}): {e}")

    prog.progress(1.0, text="All done!")

    # Persist to disk
    saved = _load_saved()
    saved.extend(new_rows)
    _save_results(saved)
    st.session_state["multi_run_store"] = run_store

    st.success(f"✅ {len(new_rows)} simulation(s) completed and saved.")

    # ── Summary comparison table ───────────────────────────────────────────────
    st.divider()
    st.subheader("📊 Simulation summary")

    df_new = pd.DataFrame(new_rows)

    disp_cols = ["soil","vege","rainfall","irrigation",
                 "soil_evap","transp","runoff","drainage"]
    if "erosion_t_ha" in df_new.columns:
        disp_cols.append("erosion_t_ha")
    if "yield_t_ha" in df_new.columns:
        disp_cols.append("yield_t_ha")

    df_disp = df_new[[c for c in disp_cols if c in df_new.columns]].copy()
    df_disp.insert(0, "Name", [f"Sim {i+1}" for i in range(len(df_disp))])
    df_disp = df_disp.rename(columns={
        "soil": "Soil Type", "vege": "Veg Option",
        "rainfall": "Rainfall\n(mm)", "irrigation": "Irrigation\n(mm)",
        "soil_evap": "Soil Evap\n(mm)", "transp": "Transp.\n(mm)",
        "runoff": "Runoff\n(mm)", "drainage": "Drainage\n(mm)",
        "erosion_t_ha": "Erosion\n(t/ha)", "yield_t_ha": "Yield\n(t/ha)",
    })

    num_cfg = {c: st.column_config.NumberColumn(format="%.1f")
               for c in df_disp.columns if df_disp[c].dtype == float}

    sel = st.dataframe(
        df_disp,
        use_container_width=True,
        hide_index=True,
        height=min(500, 60 + len(df_disp)*40),
        on_select="rerun",
        selection_mode="single-row",
        column_config=num_cfg,
        key="multi_tbl",
    )

    # CSV download
    st.download_button(
        "⬇ Download summary (CSV)",
        df_new.to_csv(index=False).encode(),
        f"{group_name}_summary.csv", "text/csv",
        use_container_width=False,
    )

    # ── Individual drill-down ─────────────────────────────────────────────────
    if sel and sel.selection.rows:
        row_idx  = sel.selection.rows[0]
        row      = new_rows[row_idx]
        key      = f"{row['soil']}||{row['vege']}"
        run_data = (st.session_state.get("multi_run_store") or {}).get(key)

        st.divider()
        st.subheader(f"📈 Sim {row_idx+1}: {row['soil']} × {row['vege']}")

        if run_data:
            _monthly_chart(
                run_data["mon"], run_data["ann"],
                clim_label, row["soil"], row["vege"],
                yr_start, yr_end,
            )
            _ann_table(run_data["ann"])
        else:
            st.info("Re-run the simulation to view the individual chart.")
