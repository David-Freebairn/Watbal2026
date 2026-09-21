"""
pages/1_Setup_scenarios.py
==========================
Step 1 — Select climate (SILO or local P51)
Step 2 — Select scenarios (single or multiple) + Run simulation
"""

import sys, json
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

import streamlit as st
st.set_page_config(page_title="Setup scenarios", page_icon="⚙️", layout="wide")

st.markdown("""
<style>
h1,h2,h3{white-space:nowrap!important;overflow:visible!important;}
.block-container{padding-top:1rem;}
</style>
""", unsafe_allow_html=True)

import pandas as pd
import numpy as np

from core.styles import apply_styles, load_station, set_station
from core.silo import (ensure_climate_cached, SiloUnavailableError,
                       slice_climate, search_stations, clear_stale_cache)
from core.reliability import reliability_color, reliability_label, _load as _load_rel

apply_styles()
clear_stale_cache(max_age_days=7)

# ── Paths ─────────────────────────────────────────────────────────────────────
_here = Path(__file__).resolve()
ROOT  = _here.parent.parent
if not (ROOT / 'core').exists() and (Path.cwd() / 'core').exists():
    ROOT = Path.cwd()

RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)
MULTI_FILE    = RESULTS_DIR / "multi_scenario_results.json"
_SESSION_FILE = RESULTS_DIR / "last_session.json"

def _find_dir(root, candidates):
    root = Path(root)
    for name in candidates:
        d = root / name
        if d.exists(): return d
    try:
        for item in root.iterdir():
            if item.is_dir() and item.name.lower() in [c.lower() for c in candidates]:
                return item
    except Exception:
        pass
    return root / candidates[0]

SOILS_DIR = _find_dir(ROOT, ["Soils", "Soil", "soils", "soil"])
VEGE_DIR  = _find_dir(ROOT, ["Vegetation descriptions", "Vegetation",
                              "Vege", "vege", "vegetation descriptions", "vegetation"])
CLIM_DIR  = _find_dir(ROOT, ["Climate files", "Climate", "climate files",
                              "climate", "Data"])

def _scan(folder, exts):
    folder = Path(folder)
    if not folder.exists(): return []
    return sorted([f for f in folder.rglob("*") if f.suffix.lower() in exts])

soil_files = _scan(SOILS_DIR, {".soil", ".prm", ".xlsx", ".xml"})
vege_files = _scan(VEGE_DIR,  {".vege", ".xlsx"})
p51_files  = _scan(CLIM_DIR,  {".p51"})

# ── Session persistence ───────────────────────────────────────────────────────
def _save_session():
    data = {}
    mode = st.session_state.get("_home_mode")
    if mode == "local":
        data = {
            "mode":          "local",
            "climate_label": st.session_state.get("climate_label", ""),
            "p51_path":      st.session_state.get("climate_p51_path", ""),
            "p51_info":      st.session_state.get("climate_p51_info", {}),
        }
    elif mode == "silo":
        stn = st.session_state.get("we_station")
        if stn:
            data = {
                "mode":          "silo",
                "climate_label": stn.get("name", ""),
                "station":       stn,
            }
    if data:
        _SESSION_FILE.write_text(json.dumps(data, indent=2))

def _load_session():
    if not _SESSION_FILE.exists():
        return
    try:
        data = json.loads(_SESSION_FILE.read_text())
        mode = data.get("mode")
        if st.session_state.get("_home_mode"):
            return
        if mode == "local" and data.get("p51_path"):
            p = Path(data["p51_path"])
            if p.exists():
                st.session_state["_home_mode"]       = "local"
                st.session_state["climate_source"]   = "local"
                st.session_state["climate_label"]    = data.get("climate_label", "")
                st.session_state["climate_p51_path"] = data["p51_path"]
                st.session_state["climate_p51_info"] = data.get("p51_info", {})
        elif mode == "silo" and data.get("station"):
            set_station(data["station"])
            st.session_state["_home_mode"]     = "silo"
            st.session_state["climate_source"] = "silo"
            st.session_state["climate_label"]  = data.get("climate_label", "")
    except Exception:
        pass

@st.cache_data(show_spinner=False)
def _peek(path_str):
    try:
        from core.read_p51 import read_p51
        _, df = read_p51(Path(path_str))
        return {
            "start": df.index.min().strftime("%Y-%m-%d"),
            "end":   df.index.max().strftime("%Y-%m-%d"),
            "years": df.index.year.nunique(),
            "rain":  round(float(df["rain"].resample("YE").sum().mean()), 0),
        }
    except Exception as e:
        return {"error": str(e)}

def _load_saved() -> list:
    if MULTI_FILE.exists():
        try:
            return json.loads(MULTI_FILE.read_text())
        except Exception:
            return []
    return []

def _save_results(results: list):
    MULTI_FILE.write_text(json.dumps(results, indent=2, default=str))

# Restore session on startup
_load_session()

# ── Page header ───────────────────────────────────────────────────────────────
hcol1, hcol2 = st.columns([9, 1])
with hcol1:
    st.markdown("## 💧 Waterbal2026")
    st.markdown(
        "<p style='color:#666;font-size:0.95rem;margin-top:-10px;'>"
        "PERFECT/HowLeaky soil water balance — compare locations, soils and land management"
        "</p>",
        unsafe_allow_html=True
    )
with hcol2:
    if st.button("ℹ️ Info", use_container_width=True):
        st.session_state["_show_info"] = not st.session_state.get("_show_info", False)

if st.session_state.get("_show_info"):
    st.markdown("""
---
### 💧 WATBAL2026

**Why?**
Watbal2026 is a reenactment of previous water balance models using Claude to build Python script to explore the logistics of building a stand-alone model that captures the more important functions of previous developments.

**How?**
Watbal2026 applies a water balance model to quantitatively describe hydrologic and water quality implications of various climate–soil–vegetation interactions. Calculations are performed on a daily time step using inputs of rainfall, pan evaporation, temperature, crop and soil management descriptions.
Parameters describing soil water, infiltration, deep drainage and erosion are based on experience from instrumented catchment studies over the last 40 years (Thomas et al. 2007).
This approach builds on previous models: PERFECT (Littleboy et al., 1992); APSIM (McCown et al., 1996) and HowLeaky (Freebairn et al., 2002).

**Getting started**

- **Step 1.** Select a climate station either from a local file (.p51 format) or from SILO. A default site will be available but other sites are available from a drop-down.
- **Step 2.** Either select a single scenario (a soil and a vegetation description), or multiple soil and vegetation combinations.

**Run simulation(s)** will provide a graphic and table summarising water balance components. Annual and monthly summaries can be downloaded, while soil and vegetation descriptions are available for a single simulation.

If a multiple scenario is applied, a summary table and download options are provided. Watbal2026 stores previous simulation summaries until they are cleared.

**Daily diagnostics** supports graphical inspection of daily components of a simulation with several output options available.

---
**Some history**

Littleboy M, Silburn DM, Freebairn DM, Woodruff DR, Hammer GL and Leslie JK (1992) Impact of soil erosion on production in cropping systems. I. Development and validation of a simulation model. *Australian Journal of Soil Research* 30, 757–74.

McCown RL, Hammer GL, Hargreaves JNG, Holzworth DP, and Freebairn DM (1996). APSIM: A novel software system for model development, model testing and simulation in agricultural systems research. *Agricultural Systems*, 50: 255–271.

Freebairn D, McClymont D, Rattray D, Owens J, Robinson B, Silburn M, Littleboy M (2002) HowLeaky? An instructive model for exploring the impact of different land uses on water balance and water quality. ASSSI Future Soils Conference, Perth 2–6 December 2002.

HowLeaky, Open Source Water Modelling Platform (2021) https://howleaky.com. University of Southern Queensland & Queensland Government.

Queensland Department of Environment and Science (2019). HowLeaky Model V5 Documentation: Version 1.04. Toowoomba. https://howleaky.com/Library/Details/064ee938-df47-4e21-ad35-aba6eaf0efb5

Ghahramani A, Freebairn DM, Sena DR, Cutajar JL, Silburn DM (2020) A pragmatic parameterisation and calibration approach to model hydrology and water quality of agricultural landscapes and catchments. *Environmental Modelling and Software* 130, 104733.

Thomas GA, Titmarsh GW, Freebairn DM, Radford BJ (2007). No-tillage and conservation farming practices in grain growing areas of Queensland – a review of 40 years of development. *Aust. J. Experimental Agric.* 47(8): 887–898.

---
"""
    )

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: Climate selection
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("### Step 1:  Select climate")

mode = st.session_state.get("_home_mode")

# ── Source toggle buttons ─────────────────────────────────────────────────────
c1, c2 = st.columns(2)
with c1:
    silo_btn = st.button(
        "🌐  SILO station\nSearch by name or pick from map",
        width="stretch",
        type="primary" if mode == "silo" else "secondary",
    )
with c2:
    local_btn = st.button(
        "📂  Local P51 file\nFrom Climate files/ folder",
        width="stretch",
        type="primary" if mode == "local" else "secondary",
    )

if silo_btn:
    st.session_state["_home_mode"] = "silo"
    st.rerun()
if local_btn:
    st.session_state["_home_mode"] = "local"
    st.session_state.pop("we_station", None)
    st.rerun()

if mode is None:
    st.caption("Choose a climate data source above.")
    st.stop()

# ── Year range (shared, shown after source selection) ─────────────────────────
# These are set inside the mode blocks below and used in Step 2 / run

yr_start = None
yr_end   = None

# ══════════════════════════════════════════════════════════════════════════════
# LOCAL P51 MODE
# ══════════════════════════════════════════════════════════════════════════════
if mode == "local":
    if not p51_files:
        st.error(
            "No .P51 files found in `Climate files/`. "
            "Download from https://www.longpaddock.qld.gov.au/silo/ "
            "and save in the `Climate files/` folder."
        )
        st.stop()

    file_labels, file_infos = [], []
    for f in p51_files:
        info = _peek(str(f))
        file_infos.append(info)
        if "error" not in info:
            file_labels.append(
                f"{f.stem}  ·  {info['start'][:4]}-{info['end'][:4]}"
                f"  ({info['years']} yr)  ·  ~{info['rain']:.0f} mm/yr"
            )
        else:
            file_labels.append(f"{f.stem}  ·  (error reading file)")

    last_path = st.session_state.get("climate_p51_path", "")
    last_idx  = 0
    for i, f in enumerate(p51_files):
        if str(f) == last_path:
            last_idx = i
            break

    _p51_row = st.columns([3, 1, 1])
    with _p51_row[0]:
        sel_idx = st.selectbox(
            "Climate file",
            range(len(p51_files)),
            index=last_idx,
            format_func=lambda i: file_labels[i],
            key="p51_sel",
            label_visibility="collapsed",
        )
    chosen_file = p51_files[sel_idx]
    chosen_info = file_infos[sel_idx]

    if "error" in chosen_info:
        st.error(f"Cannot read: {chosen_info['error']}")
        st.stop()

    _avail_s = int(chosen_info.get("start", "1900")[:4])
    _avail_e = int(chosen_info.get("end", "2026")[:4])
    with _p51_row[1]:
        yr_start = st.number_input("Start year", _avail_s, _avail_e,
                                    max(_avail_s, min(_avail_e,
                                    st.session_state.get("_last_yr_start", max(1975, _avail_s)))),
                                    step=1, key="home_yr_start")
        st.session_state["_last_yr_start"] = yr_start
    with _p51_row[2]:
        yr_end = st.number_input("End year", _avail_s, _avail_e,
                                  max(_avail_s, min(_avail_e,
                                  st.session_state.get("_last_yr_end", min(2000, _avail_e)))),
                                  step=1, key="home_yr_end")
        st.session_state["_last_yr_end"] = yr_end

    # Auto-store on change
    prev_path = st.session_state.get("climate_p51_path", "")
    if str(chosen_file) != prev_path:
        st.session_state["climate_source"]   = "local"
        st.session_state["climate_p51_path"] = str(chosen_file)
        st.session_state["climate_p51_info"] = chosen_info
        st.session_state["climate_label"]    = chosen_file.stem
        st.session_state.pop("we_station", None)
        _save_session()

    # Ensure session state is current
    st.session_state["climate_source"]   = "local"
    st.session_state["climate_p51_path"] = str(chosen_file)
    st.session_state["climate_p51_info"] = chosen_info
    st.session_state["climate_label"]    = chosen_file.stem

    clim_source = "local"
    clim_label  = chosen_file.stem
    avail_start = _avail_s
    avail_end   = _avail_e

# ══════════════════════════════════════════════════════════════════════════════
# SILO MODE
# ══════════════════════════════════════════════════════════════════════════════
elif mode == "silo":
    station = st.session_state.get("we_station")

    # Search
    def _do_search():
        q = st.session_state.get("_silo_q", "").strip()
        if not q:
            return
        with st.spinner(f"Searching for '{q}'..."):
            try:
                st.session_state["_silo_results"] = search_stations(q)
            except Exception as e:
                st.error(f"Search failed: {e}")
                st.session_state["_silo_results"] = []

    _sq_cols = st.columns([4, 1])
    with _sq_cols[0]:
        st.text_input(
            "Search by station name",
            placeholder="e.g.  Dalby  or  Goondiwindi",
            key="_silo_q",
            on_change=_do_search,
            label_visibility="collapsed",
        )
    with _sq_cols[1]:
        if st.button("Search SILO", key="silo_search_btn", use_container_width=True):
            _do_search()

    results = st.session_state.get("_silo_results", [])
    if results:
        rel_df = _load_rel()
        st.markdown(f"**{len(results)} station(s) found:**")
        for s in results[:15]:
            pct = None
            if rel_df is not None:
                try:
                    pct = float(rel_df.loc[int(s["id"]), "pct_observed"])
                except Exception:
                    pass
            lbl = (f"📌  {s['name']}  [{s['state']}]  "
                   f"({s['lat']:.3f}, {s['lon']:.3f})  —  {reliability_label(pct)}")
            if st.button(lbl, key=f"pick_{s['id']}", use_container_width=True):
                set_station(s)
                st.session_state["climate_source"] = "silo"
                st.session_state["climate_label"]  = s["name"]
                st.session_state["_silo_results"]  = []
                st.session_state["_last_silo_id"]  = s["id"]
                _save_session()
                st.rerun()

    if station:
        rel_df = _load_rel()
        pct = None
        if rel_df is not None:
            try:
                pct = float(rel_df.loc[int(station["id"]), "pct_observed"])
            except Exception:
                pass
        st.success(
            f"**{station['name']}**  [{station.get('state', '')}]  "
            f"({station['lat']:.3f}, {station['lon']:.3f})  "
            f"·  {reliability_label(pct)}"
        )

        # Prefetch climate data
        _ck = f"_prefetched_{station['id']}"
        if not st.session_state.get(_ck):
            with st.spinner("Fetching climate data from SILO..."):
                try:
                    ensure_climate_cached(
                        station["id"], lat=station["lat"], lon=station["lon"],
                        session_state=st.session_state,
                    )
                    st.session_state[_ck] = True
                    _save_session()
                    st.caption("Climate data ready.")
                except Exception as e:
                    st.warning(f"Prefetch failed (will retry on run): {e}")
        else:
            st.caption("Climate data cached and ready.")

        _silo_yr_cols = st.columns([2, 1, 1])
        with _silo_yr_cols[1]:
            yr_start = st.number_input("Start year", 1889, 2026,
                                        max(1889, min(2026,
                                        st.session_state.get("_last_yr_start", 1976))),
                                        step=1, key="home_silo_yr_start")
            st.session_state["_last_yr_start"] = yr_start
        with _silo_yr_cols[2]:
            yr_end = st.number_input("End year", 1889, 2026,
                                      max(1889, min(2026,
                                      st.session_state.get("_last_yr_end", 1993))),
                                      step=1, key="home_silo_yr_end")
            st.session_state["_last_yr_end"] = yr_end
        if yr_start and yr_end:
            yrs = yr_end - yr_start + 1
            if yrs > 0:
                st.caption(f"{yrs} years  ({yr_start}–{yr_end})")

        if st.button("Change station", key="change_stn"):
            st.session_state.pop("we_station", None)
            st.rerun()

        # Map
        rel_df = _load_rel()
        if rel_df is not None:
            with st.expander("Station reliability map", expanded=False):
                map_data = rel_df.reset_index().copy()
                map_data = map_data[
                    map_data["lat"].notna() & map_data["lon"].notna() &
                    (map_data["lat"] > -45) & (map_data["lat"] < -10) &
                    (map_data["lon"] > 110) & (map_data["lon"] < 155)
                ].copy()

                f1, f2, f3 = st.columns([2, 2, 2])
                with f1:
                    states = ["All"] + sorted(rel_df["state"].dropna().unique().tolist())
                    filter_state = st.selectbox("Filter by state", states, key="map_state")
                with f2:
                    min_pct = st.slider("Min. % observed", 0, 100, 0, step=10, key="map_pct")
                with f3:
                    filtered = map_data.copy()
                    if filter_state != "All":
                        filtered = filtered[filtered["state"] == filter_state]
                    filtered = filtered[filtered["pct_observed"] >= min_pct]
                    st.metric("Stations shown", len(filtered))

                map_data = filtered

                import plotly.express as px

                def _rel_label(pct):
                    if pct >= 90:  return ">=90%  reliable"
                    if pct >= 50:  return "50-89%  patchy"
                    return "<50%  poor"

                _plot = map_data[["lat","lon","name","station_id","pct_observed"]].copy()
                _plot["Reliability"] = _plot["pct_observed"].apply(_rel_label)
                _plot["hover"] = (
                    _plot["name"] + "  (#" +
                    _plot["station_id"].astype(str) + ")  " +
                    _plot["pct_observed"].round(0).astype(int).astype(str) + "% observed"
                )
                _cmap = {
                    ">=90%  reliable": "#2e7d32",
                    "50-89%  patchy":  "#e8a33d",
                    "<50%  poor":      "#c0392b",
                }
                _order = [">=90%  reliable", "50-89%  patchy", "<50%  poor"]

                sel_stn = st.session_state.get("we_station")
                if sel_stn:
                    import pandas as _pd
                    _sel_row = _pd.DataFrame([{
                        "lat": sel_stn["lat"], "lon": sel_stn["lon"],
                        "name": sel_stn["name"], "station_id": sel_stn.get("id", ""),
                        "pct_observed": 100,
                        "Reliability": "Selected",
                        "hover": f"SELECTED: {sel_stn['name']}",
                    }])
                    _plot = _pd.concat([_plot, _sel_row], ignore_index=True)
                    _cmap["Selected"] = "#FFD700"
                    _order = ["Selected"] + _order

                fig_map = px.scatter_map(
                    _plot, lat="lat", lon="lon",
                    color="Reliability",
                    color_discrete_map=_cmap,
                    category_orders={"Reliability": _order},
                    hover_name="hover",
                    hover_data={"lat": False, "lon": False,
                                "Reliability": False, "pct_observed": False},
                    zoom=4, center={"lat": -27.0, "lon": 134.0},
                    height=520,
                )
                for trace in fig_map.data:
                    if trace.name == "Selected":
                        trace.marker.size = 20
                        trace.marker.opacity = 1.0
                        trace.marker.symbol = "star"
                    else:
                        trace.marker.size = 8
                        trace.marker.opacity = 0.75
                fig_map.update_layout(
                    map_style="open-street-map",
                    margin=dict(l=0, r=0, t=0, b=0),
                    legend=dict(
                        title="Reliability", orientation="h",
                        x=0.5, xanchor="center", y=-0.06,
                        font=dict(size=12),
                    ),
                )
                _map_evt = st.plotly_chart(
                    fig_map, use_container_width=True,
                    on_select="rerun", selection_mode="points",
                    key="station_map",
                )
                if (_map_evt and hasattr(_map_evt, "selection") and
                        _map_evt.selection.points):
                    pt = _map_evt.selection.points[0]
                    clat = pt.get("lat") or pt.get("y")
                    clon = pt.get("lon") or pt.get("x")
                    if clat and clon:
                        import numpy as np
                        dists = ((map_data["lat"] - clat)**2 +
                                 (map_data["lon"] - clon)**2)
                        nearest = map_data.iloc[dists.argmin()]
                        s_info = {
                            "id":    int(nearest["station_id"]),
                            "name":  nearest["name"],
                            "label": f"{nearest['name']}  [{nearest['state']}]",
                            "lat":   float(nearest["lat"]),
                            "lon":   float(nearest["lon"]),
                            "state": nearest["state"],
                        }
                        set_station(s_info)
                        st.session_state["climate_source"] = "silo"
                        st.session_state["climate_label"]  = nearest["name"]
                        _save_session()
                        st.rerun()

                st.caption("Green >=90% observed  Amber 50-89%  Red <50%  Gold = selected")

            with st.expander("Browse stations table"):
                if rel_df is not None:
                    import pandas as _pd
                    disp = rel_df.reset_index()[
                        ["station_id","name","state","lat","lon",
                         "pct_observed","first_date","last_date"]
                    ].sort_values("pct_observed", ascending=False)
                    sel_tbl = st.dataframe(
                        disp, use_container_width=True, height=260,
                        on_select="rerun", selection_mode="single-row", hide_index=True,
                    )
                    if sel_tbl and sel_tbl.selection.rows:
                        row = disp.iloc[sel_tbl.selection.rows[0]]
                        s_info = {
                            "id":    int(row["station_id"]),
                            "name":  row["name"],
                            "label": f"{row['name']}  [{row['state']}]",
                            "lat":   float(row["lat"]),
                            "lon":   float(row["lon"]),
                            "state": row["state"],
                        }
                        set_station(s_info)
                        st.session_state["climate_source"] = "silo"
                        st.session_state["climate_label"]  = row["name"]
                        _save_session()
                        st.rerun()

    else:
        st.info("Search for a station above, or pick one from the map.")
        st.stop()

    clim_source = "silo"
    clim_label  = station["name"] if station else ""
    avail_start = 1889
    avail_end   = 2026

# ── Guard: need year range ─────────────────────────────────────────────────────
if yr_start is None or yr_end is None:
    st.stop()

if yr_start >= yr_end:
    st.error("Start year must be before end year.")
    st.stop()

st.divider()

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: Scenarios
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("### Step 2:  Select scenarios")
with st.container(border=True):

    if not soil_files:
        st.error(f"No soil files found in `{SOILS_DIR.relative_to(ROOT) if SOILS_DIR.is_relative_to(ROOT) else SOILS_DIR}/`")
        st.stop()
    if not vege_files:
        st.error(f"No vegetation files found in `{VEGE_DIR.relative_to(ROOT) if VEGE_DIR.is_relative_to(ROOT) else VEGE_DIR}/`")
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
            _multi_soil_default = []
            _last_s = st.session_state.get("_last_soil_path", "")
            for _f in soil_files:
                if str(_f) == _last_s:
                    _multi_soil_default = [_f]; break
            soil_sel = st.multiselect(
                "Soils — select one or more", soil_files,
                default=_multi_soil_default,
                format_func=lambda f: f.stem,
                key="soil_multi",
                placeholder="Click to add soils...",
            )
            if soil_sel:
                st.session_state["_last_soil_path"] = str(soil_sel[0])
        else:
            _s_idx = 0
            _last_soil = st.session_state.get("_last_soil_path", "")
            for _i, _f in enumerate(soil_files):
                if str(_f) == _last_soil: _s_idx = _i; break
            s = st.selectbox("Soil", soil_files, index=_s_idx,
                             format_func=lambda f: f.stem, key="soil_single")
            if s: st.session_state["_last_soil_path"] = str(s)
            soil_sel = [s] if s else []

    with c2:
        st.markdown("**Vegetation**")
        if multiple:
            _multi_vege_default = []
            _last_v = st.session_state.get("_last_vege_path", "")
            for _f in vege_files:
                if str(_f) == _last_v:
                    _multi_vege_default = [_f]; break
            vege_sel = st.multiselect(
                "Vegetation — select one or more", vege_files,
                default=_multi_vege_default,
                format_func=lambda f: f.stem,
                key="vege_multi",
                placeholder="Click to add vegetation...",
            )
            if vege_sel:
                st.session_state["_last_vege_path"] = str(vege_sel[0])
        else:
            _v_idx = 0
            _last_vege = st.session_state.get("_last_vege_path", "")
            for _i, _f in enumerate(vege_files):
                if str(_f) == _last_vege: _v_idx = _i; break
            v = st.selectbox("Vegetation", vege_files, index=_v_idx,
                             format_func=lambda f: f.stem, key="vege_single")
            if v: st.session_state["_last_vege_path"] = str(v)
            vege_sel = [v] if v else []

    n_combos = len(soil_sel) * len(vege_sel)
    if multiple and n_combos > 0:
        st.info(f"→ **{n_combos} simulation(s)**  "
                f"({len(soil_sel)} soil{'s' if len(soil_sel)>1 else ''} × "
                f"{len(vege_sel)} vegetation)")

    if multiple:
        station = load_station()
        _clim_prefix = (station['name'].split()[0] if clim_source == 'silo' and station
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

# Date stamp for filenames
_sim_date = datetime.now().strftime("%Y%m%d")

# ── Load climate ──────────────────────────────────────────────────────────────
station = load_station()
with st.spinner("Loading climate data…"):
    try:
        if clim_source == "silo":
            df_full = ensure_climate_cached(
                station["id"], lat=station["lat"], lon=station["lon"],
                session_state=st.session_state,
            )
            met_df     = slice_climate(df_full, f"{yr_start}0101", f"{yr_end}1231")
        else:
            from core.read_p51 import read_p51
            p51_path  = st.session_state.get("climate_p51_path")
            _, met_df = read_p51(Path(p51_path))
            met_df    = met_df[f"{yr_start}0101":f"{yr_end}1231"]
    except SiloUnavailableError as e:
        st.error(f"SILO unavailable: {e}")
        st.info("Try selecting a local P51 file instead.")
        st.stop()
    except Exception as e:
        st.error(f"Climate load failed: {e}")
        st.stop()

if len(met_df) == 0:
    st.error(f"No climate data in range {yr_start}–{yr_end}. Check the file covers this period.")
    st.stop()

nyears = met_df.index.year.nunique()

# ── Helpers ───────────────────────────────────────────────────────────────────
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

def _monthly_chart(mon, ann, clim_label, soil_name, vege_name, yr_start, yr_end, key_suffix=""):
    import plotly.graph_objects as go

    st.markdown(
        f"<div style='background:#f0f0f0;padding:8px 16px;border-radius:6px;"
        f"font-size:1.1rem;font-weight:600;margin-bottom:4px;white-space:nowrap;'>"
        f"Climate: &nbsp;{clim_label} &nbsp;&nbsp;&nbsp;&nbsp;"
        f"Soil type: &nbsp;{soil_name} &nbsp;&nbsp;&nbsp;&nbsp;"
        f"Vegetation: &nbsp;{vege_name}"
        f"</div>",
        unsafe_allow_html=True,
    )

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
            text=(f"Water balance summary  {yr_start}-{yr_end}  ·  "
                  f"Rain {rm:.0f}mm  "
                  f"Runoff {ann['runoff'].mean():.0f}mm  "
                  f"Evap {ann['soil_evap'].mean():.0f}mm  "
                  f"Transp {ann['transp'].mean():.0f}mm  "
                  f"Drain {ann['drainage'].mean():.0f}mm"),
            x=0.5, xanchor="center", font=dict(size=13),
        ),
        height=430, margin=dict(l=55, r=60, t=55, b=90),
        legend=dict(orientation="h", x=0.5, xanchor="center", y=-0.22,
                    font=dict(size=11)),
        plot_bgcolor="white", paper_bgcolor="white",
        hovermode="x unified", bargap=0.25,
        yaxis=dict(title="mm / month", rangemode="tozero",
                   gridcolor="rgba(0,0,0,0.06)", tickfont=dict(size=11)),
        yaxis2=dict(title="Erosion (t/ha/month)", overlaying="y", side="right",
                    rangemode="tozero", showgrid=False, tickfont=dict(size=11)),
        xaxis=dict(showgrid=False, tickfont=dict(size=12)),
    )

    # Export button
    _fig_key = f"monthly_fig{key_suffix}"
    ec1, ec2 = st.columns([5, 1])
    with ec1:
        st.plotly_chart(fig, width="stretch", key=_fig_key)
    with ec2:
        try:
            _png = fig.to_image(format="png", width=1200, height=430)
            _safe = f"{clim_label}_{soil_name}_{vege_name}_{yr_start}_{yr_end}_{_sim_date}".replace(" ","_")[:80]
            st.download_button("⬇ PNG", _png, f"{_safe}_monthly.png",
                               "image/png", use_container_width=True,
                               key=f"dl_png{key_suffix}")
        except Exception:
            pass  # kaleido not installed

def _ann_table(ann, vege_path=None, key_suffix=""):
    rain_m = float(ann["rain"].mean())
    rows = []

    for k, label in [("rain","Rainfall"),("runoff","Runoff"),
                     ("soil_evap","Soil evaporation"),("transp","Transpiration"),
                     ("et","Total ET"),("drainage","Deep drainage")]:
        v  = float(ann[k].mean())
        cv = float(ann[k].std() / max(ann[k].mean(), 0.1) * 100)
        rows.append({
            "Component":   label,
            "Mean mm/yr":  int(round(v)),
            "% rain":      f"{v/rain_m*100:.0f}",
            "CV":          f"{cv:.0f}",
            "Min":         int(round(float(ann[k].min()))),
            "Max":         int(round(float(ann[k].max()))),
        })

    if "sediment" in ann.columns:
        sv = float(ann["sediment"].mean())
        rows.append({
            "Component":   "Erosion (t/ha)",
            "Mean mm/yr":  round(sv, 2),
            "% rain":      "—",
            "CV":          f"{float(ann['sediment'].std()/max(sv,0.01)*100):.0f}",
            "Min":         round(float(ann["sediment"].min()), 2),
            "Max":         round(float(ann["sediment"].max()), 2),
        })

    yd = ann.attrs.get("annual_yield", {})
    if yd:
        yv = [v for v in yd.values() if v > 0]
        if yv:
            rows.append({
                "Component":   "Yield (t/ha)",
                "Mean mm/yr":  round(float(np.mean(yv)), 2),
                "% rain":      "—",
                "CV":          f"{float(np.std(yv)/max(np.mean(yv),0.01)*100):.0f}",
                "Min":         round(float(np.min(yv)), 2),
                "Max":         round(float(np.max(yv)), 2),
            })

    df_tbl = pd.DataFrame(rows)
    st.dataframe(
        df_tbl, width="stretch", hide_index=True,
        column_config={
            "Component":  st.column_config.TextColumn("Component", width="medium"),
            "Mean mm/yr": st.column_config.NumberColumn("Mean", format="%g"),
            "% rain":     st.column_config.TextColumn("% rain", width="small"),
            "CV":         st.column_config.TextColumn("CV%",    width="small"),
            "Min":        st.column_config.NumberColumn("Min",   format="%g"),
            "Max":        st.column_config.NumberColumn("Max",   format="%g"),
        },
        key=f"ann_tbl{key_suffix}",
    )
    return df_tbl

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
            import traceback
            st.error(f"Simulation failed: {e}")
            st.code(traceback.format_exc(), language="text")
            st.stop()

    st.success(f"✅ Done — {nyears} years  |  Balance error = {err:+.4f} mm/yr")

    _monthly_chart(mon, ann, clim_label, profile.name,
                   vege_path.stem, yr_start, yr_end)

    st.subheader("Annual water balance summary")
    df_ann_tbl = _ann_table(ann)

    # Save files and downloads
    safe = (f"{clim_label}_{profile.name}_{vege_path.stem}"
            f"_{yr_start}_{yr_end}_{_sim_date}").replace(" ","_")[:80]
    ann_out = ann[["rain","runoff","drainage","soil_evap","transp","et"]].copy()
    ann_out.columns = ["rain_mm","runoff_mm","drainage_mm","soil_evap_mm","transp_mm","et_mm"]
    if "sediment" in ann.columns:
        ann_out["erosion_t_ha"] = ann["sediment"].values
    yd = df_out.attrs.get("annual_yield", {})
    if yd:
        ann_out["yield_t_ha"] = ann_out.index.map(lambda y: round(yd.get(y, 0.0), 3))
    ann_out.to_csv(RESULTS_DIR / f"{safe}_annual.csv")

    st.divider()
    d1, d2, d3 = st.columns(3)
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
    with d3:
        st.download_button("⬇ Summary table CSV",
                           df_ann_tbl.to_csv(index=False).encode(),
                           f"{safe}_summary.csv", "text/csv",
                           width="stretch")

    if abs(err) < 0.001:
        st.caption(f"Balance error = {err:+.5f} mm/yr  ✅ Water balance OK")
    else:
        st.warning(f"Balance error = {err:+.5f} mm/yr  ⚠️")

    # Soil and vegetation spec viewer
    st.divider()
    sv1, sv2 = st.columns(2)
    with sv1:
        with st.expander("View soil specification"):
            try:
                from core.input_summaries import make_soil_summary
                import tempfile, os
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as _t:
                    _tp = _t.name
                make_soil_summary(profile, out_path=_tp)
                st.image(_tp, width="stretch")
                os.unlink(_tp)
            except Exception as _e:
                rows_s = [
                    {"Parameter":"Name",     "Value": str(profile.name)},
                    {"Parameter":"Layers",   "Value": str(len(profile.layers))},
                    {"Parameter":"PAWC mm",  "Value": f"{getattr(profile, 'pawc_total', None) or getattr(profile, 'pawc', 0):.0f}"},
                    {"Parameter":"CN2 bare", "Value": str(profile.cn2_bare)},
                    {"Parameter":"Cona",     "Value": str(profile.cona)},
                    {"Parameter":"U mm",     "Value": str(profile.u)},
                    {"Parameter":"K erod.",  "Value": str(profile.musle_k)},
                    {"Parameter":"Slope %",  "Value": str(profile.slope_pct)},
                ]
                for i, l in enumerate(profile.layers):
                    rows_s.append({"Parameter":f"L{i+1} depth mm","Value":f"{l.depth_mm:.0f}"})
                    rows_s.append({"Parameter":f"L{i+1} PAWC mm", "Value":f"{l.pawc:.0f}"})
                st.dataframe(pd.DataFrame(rows_s), hide_index=True, width="stretch")
    with sv2:
        with st.expander("View vegetation specification"):
            try:
                from core.input_summaries import make_vege_summary
                import tempfile, os
                _vobj = None
                if vege_path.suffix.lower() == ".vege":
                    from core.vege import read_vege
                    _vobj = read_vege(vege_path)
                else:
                    from core.cover_excel import read_cover_excel
                    _vobj = read_cover_excel(vege_path)
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as _t:
                    _tp = _t.name
                make_vege_summary(_vobj, out_path=_tp)
                st.image(_tp, width="stretch")
                os.unlink(_tp)
            except Exception as _e:
                st.info(f"Vegetation: {vege_path.stem}  ({_e})")

# ══════════════════════════════════════════════════════════════════════════════
# MULTIPLE SIMULATIONS
# ══════════════════════════════════════════════════════════════════════════════
else:
    total    = len(soil_sel) * len(vege_sel)
    prog     = st.progress(0, text="Running scenarios…")
    done     = 0
    new_rows = []
    run_store = {}

    for soil_path in soil_sel:
        try:
            profile = _load_profile(soil_path)
        except Exception as e:
            import traceback
            st.warning(f"Skipping soil {soil_path.stem}: {e}")
            st.code(traceback.format_exc(), language="text")
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
                    "sim_date"  : _sim_date,
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

                safe = (f"{clim_label}_{profile.name}_{vege_path.stem}"
                        f"_{yr_start}_{yr_end}_{_sim_date}").replace(" ","_")[:80]
                ann_out = ann[["rain","runoff","drainage","soil_evap","transp","et"]].copy()
                ann_out.columns = ["rain_mm","runoff_mm","drainage_mm",
                                   "soil_evap_mm","transp_mm","et_mm"]
                if "sediment" in ann.columns:
                    ann_out["erosion_t_ha"] = ann["sediment"].values
                if yd:
                    ann_out["yield_t_ha"] = ann_out.index.map(
                        lambda y: round(yd.get(y, 0.0), 3))
                ann_out.to_csv(RESULTS_DIR / f"{safe}_annual.csv")

            except Exception as e:
                st.warning(f"Sim {done} failed ({soil_path.stem} × {vege_path.stem}): {e}")

    prog.progress(1.0, text="All done!")

    saved = _load_saved()
    saved.extend(new_rows)
    _save_results(saved)
    st.session_state["multi_run_store"] = run_store

    st.success(f"✅ {len(new_rows)} simulation(s) completed and saved.")

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
        df_disp, width="stretch", hide_index=True,
        height=min(500, 60 + len(df_disp)*40),
        on_select="rerun", selection_mode="single-row",
        column_config=num_cfg, key="multi_tbl",
    )

    # Download summary
    _group_safe = group_name.replace(" ","_")
    _sum_csv = df_new.to_csv(index=False).encode()
    d1, d2 = st.columns(2)
    with d1:
        st.download_button(
            "⬇ Summary CSV",
            _sum_csv,
            f"{_group_safe}_{_sim_date}_summary.csv", "text/csv",
            width="content",
        )
    with d2:
        try:
            import io
            _buf = io.BytesIO()
            df_new.to_excel(_buf, index=False)
            st.download_button(
                "⬇ Summary Excel",
                _buf.getvalue(),
                f"{_group_safe}_{_sim_date}_summary.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="content",
            )
        except Exception:
            st.download_button(
                "⬇ Summary CSV (2)",
                _sum_csv,
                f"{_group_safe}_{_sim_date}_summary2.csv", "text/csv",
                width="content",
            )

    # Individual drill-down
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
                yr_start, yr_end, key_suffix=f"_drill{row_idx}",
            )
            _ann_table(run_data["ann"], key_suffix=f"_drill{row_idx}")
        else:
            st.info("Re-run the simulation to view the individual chart.")
