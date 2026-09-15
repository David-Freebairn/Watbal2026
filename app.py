"""
Waterbal2026 — Home
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "core"))

import streamlit as st

st.set_page_config(
    page_title="Waterbal2026",
    page_icon="💧",
    layout="wide",
    initial_sidebar_state="expanded",
)

from core.styles import apply_styles, set_station
from core.silo import search_stations, SiloUnavailableError, clear_stale_cache, ensure_climate_cached
from core.reliability import reliability_color, reliability_label, _load as _load_rel

apply_styles()
clear_stale_cache(max_age_days=7)

ROOT        = Path(__file__).parent
CLIM_DIR    = ROOT / "Climate files"
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)
_SESSION_FILE = RESULTS_DIR / "last_session.json"

def _save_session():
    """Persist current climate selection to disk."""
    import json
    data = {}
    mode = st.session_state.get("_home_mode")
    if mode == "local":
        data = {
            "mode":          "local",
            "climate_label": st.session_state.get("climate_label",""),
            "p51_path":      st.session_state.get("climate_p51_path",""),
            "p51_info":      st.session_state.get("climate_p51_info",{}),
        }
    elif mode == "silo":
        stn = st.session_state.get("we_station")
        if stn:
            data = {
                "mode":          "silo",
                "climate_label": stn.get("name",""),
                "station":       stn,
            }
    if data:
        _SESSION_FILE.write_text(json.dumps(data, indent=2))

def _load_session():
    """Restore last climate selection from disk into session state."""
    import json
    if not _SESSION_FILE.exists():
        return
    try:
        data = json.loads(_SESSION_FILE.read_text())
        mode = data.get("mode")
        # Only restore if session doesn't already have a climate
        if st.session_state.get("_home_mode"):
            return
        if mode == "local" and data.get("p51_path"):
            p = Path(data["p51_path"])
            if p.exists():
                st.session_state["_home_mode"]       = "local"
                st.session_state["climate_source"]   = "local"
                st.session_state["climate_label"]    = data.get("climate_label","")
                st.session_state["climate_p51_path"] = data["p51_path"]
                st.session_state["climate_p51_info"] = data.get("p51_info",{})
        elif mode == "silo" and data.get("station"):
            from core.styles import set_station
            set_station(data["station"])
            st.session_state["_home_mode"]      = "silo"
            st.session_state["climate_source"]  = "silo"
            st.session_state["climate_label"]   = data.get("climate_label","")
    except Exception:
        pass

def _scan_p51():
    if not CLIM_DIR.exists():
        return []
    return sorted([f for f in CLIM_DIR.rglob("*") if f.suffix.lower() == ".p51"])

# ── Restore last session on startup ──────────────────────────────────────────
_load_session()

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

# ── Header ────────────────────────────────────────────────────────────────────
# Prevent header truncation with CSS
st.markdown("""
<style>
h1, h2, h3 { white-space: nowrap !important; overflow: visible !important; }
.stMarkdown h2 { font-size: 1.5rem; }
</style>
""", unsafe_allow_html=True)

hcol1, hcol2 = st.columns([9, 1])
with hcol1:
    st.markdown("## Step 1:  Select climate  (two source options)")
with hcol2:
    if st.button("Info", use_container_width=True):
        st.session_state["_show_info"] = not st.session_state.get("_show_info", False)

if st.session_state.get("_show_info"):
    st.info(
        "**Waterbal2026** — PERFECT/HowLeaky soil water balance model for dryland cropping. "
        "Processes: SCS-CN runoff (AMC adjustment), Ritchie two-stage evaporation, "
        "transpiration, deep drainage, MUSLE erosion, yield (TUE x HI). "
        "Climate: SILO P51 files or SILO API. "
        "Ref: Littleboy et al. (1992) PERFECT QB92005. "
        "Contact: david.freebairn@gmail.com"
    )

st.divider()

# ── Climate source buttons ────────────────────────────────────────────────────
mode = st.session_state.get("_home_mode")

# Show currently selected climate as a compact banner if already chosen
current_label = None
if mode == "local":
    current_label = st.session_state.get("climate_label")
elif mode == "silo":
    stn = st.session_state.get("we_station")
    if stn:
        current_label = stn["name"]

if current_label:
    # Show compact current climate banner
    _p51_info = st.session_state.get("climate_p51_info", {})
    _period = ""
    if mode == "local" and _p51_info:
        _period = (f"  ·  {_p51_info.get('start','')[:4]}-{_p51_info.get('end','')[:4]}"
                   f"  ({_p51_info.get('years','?')} yr)"
                   f"  ~{_p51_info.get('rain','?'):.0f} mm/yr")
    elif mode == "silo":
        _stn = st.session_state.get("we_station", {})
        _period = f"  ·  ({_stn.get('lat',0):.3f}, {_stn.get('lon',0):.3f})" if _stn else ""
    bcol1, bcol2 = st.columns([5, 1])
    with bcol1:
        st.success(f"**Current climate:** {current_label}{_period}")
    with bcol2:
        if st.button("Run →", type="primary", use_container_width=True):
            st.switch_page("pages/1_Run_simulation.py")
    st.divider()

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

# ══════════════════════════════════════════════════════════════════════════════
# LOCAL P51 MODE
# ══════════════════════════════════════════════════════════════════════════════
if mode == "local":
    st.divider()
    p51_files = _scan_p51()

    if not p51_files:
        st.error(
            "No .P51 files found in `Climate files/`. "
            "Download from https://www.longpaddock.qld.gov.au/silo/ "
            "and save in the `Climate files/` folder."
        )
        st.stop()

    # Build labels
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

    # Restore last selection
    last_path = st.session_state.get("climate_p51_path", "")
    last_idx  = 0
    for i, f in enumerate(p51_files):
        if str(f) == last_path:
            last_idx = i
            break

    sel_idx = st.selectbox(
        "Select climate file",
        range(len(p51_files)),
        index=last_idx,
        format_func=lambda i: file_labels[i],
        key="p51_sel",
    )
    chosen_file = p51_files[sel_idx]
    chosen_info = file_infos[sel_idx]

    if "error" in chosen_info:
        st.error(f"Cannot read: {chosen_info['error']}")
        st.stop()

    # Auto-store selection and start prefetch immediately on change
    prev_path = st.session_state.get("climate_p51_path", "")
    if str(chosen_file) != prev_path:
        st.session_state["climate_source"]   = "local"
        st.session_state["climate_p51_path"] = str(chosen_file)
        st.session_state["climate_p51_info"] = chosen_info
        st.session_state["climate_label"]    = chosen_file.stem
        st.session_state.pop("we_station", None)
        _save_session()

    if st.button("Go to Single simulation →", type="primary", width="stretch"):
        st.session_state["climate_source"]   = "local"
        st.session_state["climate_p51_path"] = str(chosen_file)
        st.session_state["climate_p51_info"] = chosen_info
        st.session_state["climate_label"]    = chosen_file.stem
        _save_session()
        st.switch_page("pages/1_Run_simulation.py")

# ══════════════════════════════════════════════════════════════════════════════
# SILO MODE
# ══════════════════════════════════════════════════════════════════════════════
elif mode == "silo":
    st.divider()
    station = st.session_state.get("we_station")

    # ── Search ────────────────────────────────────────────────────────────────
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

    st.text_input(
        "Search by station name",
        placeholder="e.g.  Dalby  or  Goondiwindi",
        key="_silo_q",
        on_change=_do_search,
    )
    if st.button("Search SILO", key="silo_search_btn"):
        _do_search()

    # Search results
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
            lbl = f"📌  {s['name']}  [{s['state']}]  ({s['lat']:.3f}, {s['lon']:.3f})  —  {reliability_label(pct)}"
            if st.button(lbl, key=f"pick_{s['id']}", use_container_width=True):
                set_station(s)
                st.session_state["climate_source"] = "silo"
                st.session_state["climate_label"]  = s["name"]
                st.session_state["_silo_results"]  = []
                st.session_state["_last_silo_id"]  = s["id"]
                _save_session()
                st.rerun()

    # Currently selected station
    if station:
        rel_df = _load_rel()
        pct = None
        if rel_df is not None:
            try:
                pct = float(rel_df.loc[int(station["id"]), "pct_observed"])
            except Exception:
                pass
        st.success(
            f"**{station['name']}**  [{station.get('state','')}]  "
            f"({station['lat']:.3f}, {station['lon']:.3f})  "
            f"·  {reliability_label(pct)}"
        )

        # Prefetch climate data immediately
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

        bcol1, bcol2 = st.columns(2)
        with bcol1:
            if st.button("Go to Single simulation →", type="primary", width="stretch"):
                st.switch_page("pages/1_Run_simulation.py")
        with bcol2:
            if st.button("Go to Matrix simulations →", width="stretch"):
                st.switch_page("pages/2_Matrix_simulations.py")

        if st.button("Change station", key="change_stn"):
            st.session_state.pop("we_station", None)
            st.rerun()

    # ── Map (collapsed by default) ────────────────────────────────────────────
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

            sel = st.session_state.get("we_station")
            if sel:
                import pandas as _pd
                _sel_row = _pd.DataFrame([{
                    "lat": sel["lat"], "lon": sel["lon"],
                    "name": sel["name"], "station_id": sel.get("id",""),
                    "pct_observed": 100,
                    "Reliability": "Selected",
                    "hover": f"SELECTED: {sel['name']}",
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
            # Make selected station larger and distinct
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
            if _map_evt and hasattr(_map_evt, "selection") and _map_evt.selection.points:
                pt = _map_evt.selection.points[0]
                clat = pt.get("lat") or pt.get("y")
                clon = pt.get("lon") or pt.get("x")
                if clat and clon:
                    import numpy as np
                    dists = ((map_data["lat"]-clat)**2 + (map_data["lon"]-clon)**2)
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

    # ── Browse table ──────────────────────────────────────────────────────────
    with st.expander("Browse stations table"):
        if rel_df is not None:
            import pandas as _pd
            disp = rel_df.reset_index()[
                ["station_id","name","state","lat","lon","pct_observed","first_date","last_date"]
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
