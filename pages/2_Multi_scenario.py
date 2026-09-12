"""
pages/2_Multi_scenario.py
==========================
Multi-scenario runner: one climate × multiple soils × multiple vegetation files.
Produces a summary table matching the HowLeaky reference (sortable, CSV export).
Results are saved to disk and persist across sessions.
"""

import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

import streamlit as st
import pandas as pd
import numpy as np

from core.styles import apply_styles, load_station
from core.nav import HOME
from core.silo import ensure_climate_cached, SiloUnavailableError, slice_climate

apply_styles()

ROOT        = Path(__file__).parent.parent
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)
MULTI_RESULTS_FILE = RESULTS_DIR / "multi_scenario_results.json"

def _find_dir(root, candidates):
    root = Path(root)
    for name in candidates:
        d = root / name
        if d.exists():
            return d
    try:
        for item in root.iterdir():
            if item.is_dir():
                low = item.name.lower()
                for name in candidates:
                    if low == name.lower():
                        return item
    except Exception:
        pass
    return root / candidates[0]

SOILS_DIR = _find_dir(ROOT, ["Soils","Soil","soils","soil"])
VEGE_DIR  = _find_dir(ROOT, ["Vegetation descriptions","Vegetation","Vege","vege"])
CLIM_DIR  = _find_dir(ROOT, ["Climate files","Climate","climate files","climate"])

def _scan(folder, exts):
    folder = Path(folder)
    if not folder.exists():
        return []
    return sorted([f for f in folder.rglob("*") if f.suffix.lower() in exts])

soil_files = _scan(SOILS_DIR, {".soil",".prm",".xlsx",".xml"})
vege_files = _scan(VEGE_DIR,  {".vege",".xlsx"})
clim_files = _scan(CLIM_DIR,  {".p51",".met"})

# ── Load persisted results ────────────────────────────────────────────────────
def load_saved_results() -> list:
    if MULTI_RESULTS_FILE.exists():
        try:
            return json.loads(MULTI_RESULTS_FILE.read_text())
        except Exception:
            return []
    return []

def save_results(results: list):
    MULTI_RESULTS_FILE.write_text(json.dumps(results, indent=2, default=str))

# ── Header ────────────────────────────────────────────────────────────────────
st.title("📊 Multi-scenario comparison")
st.caption("Run one climate × multiple soils × multiple vegetation options. "
           "Results are saved to disk and persist between sessions.")

station = load_station()

# ── Scenario builder ──────────────────────────────────────────────────────────
with st.container(border=True):
    st.subheader("🔧 Scenario setup")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Climate**")
        clim_source = st.radio(
            "Climate source",
            ["SILO station (map)", "Local file"],
            horizontal=True,
            label_visibility="collapsed",
        )
        if clim_source == "SILO station (map)":
            if station:
                st.success(f"📍 {station['name']}")
            else:
                st.warning("No station selected.")
                st.page_link(HOME, label="← Select a station on the map")
        else:
            clim_choice = st.selectbox("Climate file", clim_files,
                                        format_func=lambda f: f.name) if clim_files \
                          else None

        cy1, cy2 = st.columns(2)
        with cy1:
            yr_start = st.number_input("Start year", 1890, 2026, 1975, step=1)
        with cy2:
            yr_end   = st.number_input("End year",   1890, 2026, 2000, step=1)

    with col2:
        st.markdown("**Soils** (select one or more)")
        soil_selected = st.multiselect(
            "Soils",
            soil_files,
            format_func=lambda f: f.stem,
            label_visibility="collapsed",
        )

        st.markdown("**Vegetation options** (select one or more)")
        vege_selected = st.multiselect(
            "Vege",
            vege_files,
            format_func=lambda f: f.stem,
            label_visibility="collapsed",
        )

        n_scenarios = len(soil_selected) * len(vege_selected)
        if n_scenarios > 0:
            st.info(f"→ **{n_scenarios} scenario(s)** will be run  "
                    f"({len(soil_selected)} soil × {len(vege_selected)} vege)")

    scenario_prefix = st.text_input(
        "Scenario group name (prefix for saved results)",
        value=f"{'Dalby' if not station else station['name'].split()[0]}_{yr_start}_{yr_end}",
    )

# ── Run button ────────────────────────────────────────────────────────────────
run_btn = st.button(
    f"▶  Run {n_scenarios} scenario(s)" if n_scenarios > 0 else "▶  Run",
    type="primary",
    use_container_width=True,
    disabled=(n_scenarios == 0),
)

if run_btn:
    errors = []
    if clim_source == "SILO station (map)" and not station:
        errors.append("No station selected.")
    if not soil_selected:
        errors.append("Select at least one soil.")
    if not vege_selected:
        errors.append("Select at least one vegetation file.")
    if yr_start >= yr_end:
        errors.append("Start year must be before end year.")
    if errors:
        for e in errors: st.error(e)
        st.stop()

    # Load climate once
    with st.spinner("Loading climate…"):
        try:
            if clim_source == "SILO station (map)":
                df_full = ensure_climate_cached(
                    station["id"], lat=station["lat"], lon=station["lon"],
                    session_state=st.session_state,
                )
                met_df = slice_climate(df_full, f"{yr_start}0101", f"{yr_end}1231")
                clim_label = station["name"]
            else:
                ext = clim_choice.suffix.lower()
                if ext == ".p51":
                    from core.read_p51 import read_p51
                    _, met_df = read_p51(clim_choice)
                else:
                    from core.perfect_io import read_met
                    _, met_df = read_met(clim_choice)
                met_df     = met_df[f"{yr_start}0101":f"{yr_end}1231"]
                clim_label = clim_choice.stem
        except Exception as e:
            st.error(f"Climate load failed: {e}")
            st.stop()

    if len(met_df) == 0:
        st.error(f"No climate data in range {yr_start}–{yr_end}.")
        st.stop()

    nyears = met_df.index.year.nunique()
    from core.run_simulation import _run_daily, _monthly_means, _annual_stats

    saved = load_saved_results()
    progress_bar = st.progress(0, text="Running scenarios…")
    total = len(soil_selected) * len(vege_selected)
    done  = 0
    new_results = []

    for si, soil_path in enumerate(soil_selected):
        # Load soil
        try:
            ext = soil_path.suffix.lower()
            if ext == ".soil":
                from core.soil_xml import read_soil_xml
                profile = read_soil_xml(soil_path)
            elif ext in (".xlsx",".xls"):
                from core.soil_excel import read_soil_excel
                profile = read_soil_excel(soil_path)
            else:
                from core.soil import read_prm
                profile = read_prm(soil_path)
        except Exception as e:
            st.warning(f"Skipping soil {soil_path.stem}: {e}")
            continue

        for vi, vege_path in enumerate(vege_selected):
            sim_num = done + 1
            progress_bar.progress(
                done / total,
                text=f"Sim {sim_num}/{total}: {soil_path.stem} × {vege_path.stem}",
            )

            try:
                ext = vege_path.suffix.lower()
                if ext == ".vege":
                    from core.vege import read_vege
                    from core.run_simulation import _make_vege_fn
                    get_state = _make_vege_fn(read_vege(vege_path))
                else:
                    from core.cover_excel import read_cover_excel
                    from core.run_simulation import _make_cover_fn
                    get_state = _make_cover_fn(read_cover_excel(vege_path))

                df_out, sw0, swf = _run_daily(met_df, profile, get_state)
                dsw    = swf - sw0
                err    = (df_out.rain.sum() - df_out.runoff.sum()
                          - df_out.drainage.sum() - df_out.soil_evap.sum()
                          - df_out.transp.sum() - dsw) / nyears
                ann    = _annual_stats(df_out)
                mon    = _monthly_means(df_out, nyears)

                row = {
                    "sim_id"     : f"Sim {len(saved)+len(new_results)+1}",
                    "group"      : scenario_prefix,
                    "climate"    : clim_label,
                    "soil"       : profile.name,
                    "vege"       : vege_path.stem,
                    "start"      : yr_start,
                    "end"        : yr_end,
                    "nyears"     : nyears,
                    "rainfall"   : round(float(ann["rain"].mean()),    1),
                    "runoff"     : round(float(ann["runoff"].mean()),   1),
                    "soil_evap"  : round(float(ann["soil_evap"].mean()),1),
                    "transp"     : round(float(ann["transp"].mean()),   1),
                    "et"         : round(float(ann["et"].mean()),       1),
                    "drainage"   : round(float(ann["drainage"].mean()), 1),
                    "irrigation" : 0.0,
                    "err_yr"     : round(err, 4),
                }
                if "sediment" in ann.columns:
                    row["erosion_t_ha"] = round(float(ann["sediment"].mean()), 2)
                yd = df_out.attrs.get("annual_yield", {})
                if yd:
                    row["yield_t_ha"] = round(sum(yd.values())/len(yd), 2)

                # Save annual CSV
                ann_out = ann[["rain","runoff","drainage","soil_evap","transp","et"]].copy()
                ann_out.columns = ["rain_mm","runoff_mm","drainage_mm","soil_evap_mm","transp_mm","et_mm"]
                safe = f"{scenario_prefix}_{soil_path.stem}_{vege_path.stem}".replace(" ","_")[:60]
                ann_out.to_csv(RESULTS_DIR / f"{safe}_annual.csv")

                new_results.append(row)

            except Exception as e:
                st.warning(f"Sim {sim_num} failed: {e}")

            done += 1

    progress_bar.progress(1.0, text="Done!")

    # Persist
    saved.extend(new_results)
    save_results(saved)
    st.session_state["multi_results"] = saved
    st.success(f"✅ {len(new_results)} scenario(s) completed and saved.")
    st.rerun()

# ── Display results table ─────────────────────────────────────────────────────
saved = st.session_state.get("multi_results") or load_saved_results()

if saved:
    st.divider()
    st.subheader("📋 Simulation results")

    df_res = pd.DataFrame(saved)

    # Display selector
    disp_cols_options = {
        "Annual Water Balance Summary": [
            "sim_id","group","climate","soil","vege",
            "rainfall","irrigation","soil_evap","transp","runoff","drainage",
        ],
        "With erosion & yield": [
            "sim_id","soil","vege","rainfall","runoff","soil_evap","transp",
            "drainage","erosion_t_ha","yield_t_ha",
        ],
        "All columns": list(df_res.columns),
    }

    tc1, tc2, tc3 = st.columns([3, 2, 1])
    with tc1:
        disp_key = st.selectbox("Display", list(disp_cols_options.keys()))
    with tc2:
        search   = st.text_input("Search", placeholder="Filter by soil / vege / group…")
    with tc3:
        st.markdown("<br>", unsafe_allow_html=True)
        csv_bytes = df_res.to_csv(index=False).encode()
        st.download_button("⬇ CSV", csv_bytes, "multi_scenario_results.csv",
                           "text/csv", width="stretch")

    # Filter
    if search:
        mask = df_res.apply(
            lambda row: any(search.lower() in str(v).lower() for v in row), axis=1
        )
        df_show = df_res[mask]
    else:
        df_show = df_res.copy()

    # Select columns
    cols = [c for c in disp_cols_options[disp_key] if c in df_show.columns]
    df_show = df_show[cols]

    # Rename for display
    rename = {
        "sim_id":"Name", "group":"Group", "climate":"Climate",
        "soil":"Soil Type", "vege":"Veg Option",
        "rainfall":"Rainfall\n(mm)", "irrigation":"Irrigation\n(mm)",
        "soil_evap":"Soil Evap\n(mm)", "transp":"Transp.\n(mm)",
        "runoff":"Runoff\n(mm)", "drainage":"Drainage\n(mm)",
        "erosion_t_ha":"Erosion\n(t/ha)", "yield_t_ha":"Yield\n(t/ha)",
        "start":"Start","end":"End","nyears":"Years",
    }
    df_show = df_show.rename(columns={k:v for k,v in rename.items() if k in df_show.columns})

    st.dataframe(
        df_show,
        use_container_width=True,
        hide_index=True,
        height=min(600, 60 + len(df_show)*38),
        column_config={
            "Rainfall\n(mm)":  st.column_config.NumberColumn(format="%.0f"),
            "Runoff\n(mm)":    st.column_config.NumberColumn(format="%.1f"),
            "Soil Evap\n(mm)": st.column_config.NumberColumn(format="%.0f"),
            "Transp.\n(mm)":   st.column_config.NumberColumn(format="%.0f"),
            "Drainage\n(mm)":  st.column_config.NumberColumn(format="%.1f"),
            "Erosion\n(t/ha)": st.column_config.NumberColumn(format="%.2f"),
            "Yield\n(t/ha)":   st.column_config.NumberColumn(format="%.2f"),
        },
    )

    st.caption(f"{len(df_show)} scenario(s) shown  ·  "
               f"Saved to `{MULTI_RESULTS_FILE.name}`")

    # ── Clear button ──────────────────────────────────────────────────────────
    with st.expander("⚠️ Manage saved results"):
        if st.button("🗑 Clear all saved results", type="secondary"):
            save_results([])
            st.session_state.pop("multi_results", None)
            st.success("Results cleared.")
            st.rerun()

else:
    st.info("No results yet. Configure scenarios above and click Run.")
