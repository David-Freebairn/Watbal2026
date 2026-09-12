"""
Waterbal2026 — Home / Landing page
====================================
Simple entry point. No map shown until user explicitly chooses SILO.
Routes to:
  - SILO station selection (map + search)
  - Local P51 file selection (scans Climate files/ folder)
Then navigates to Run simulation.
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
from core.silo import search_stations, SiloUnavailableError, clear_stale_cache
from core.reliability import reliability_color, reliability_label, _load as _load_rel

apply_styles()
clear_stale_cache(max_age_days=7)

# ── Root folder — where Climate files/ lives ──────────────────────────────────
ROOT      = Path(__file__).parent
CLIM_DIR  = ROOT / "Climate files"

def _scan_p51():
    if not CLIM_DIR.exists():
        return []
    return sorted([f for f in CLIM_DIR.rglob("*") if f.suffix.lower() == ".p51"])

# ── Header ────────────────────────────────────────────────────────────────────
st.title("💧 Waterbal2026 — Water Balance Model Environment")
st.markdown("---")

# ── Climate source selector ───────────────────────────────────────────────────
st.subheader("Select climate data source")

src_col1, src_col2 = st.columns(2)

with src_col1:
    silo_btn = st.button(
        "🌐  SILO station\n\nSearch or pick from map",
        use_container_width=True,
        type="primary" if st.session_state.get("_home_mode") == "silo" else "secondary",
    )
with src_col2:
    local_btn = st.button(
        "📂  Local P51 file\n\nUse files in Climate files/ folder",
        use_container_width=True,
        type="primary" if st.session_state.get("_home_mode") == "local" else "secondary",
    )

if silo_btn:
    st.session_state["_home_mode"] = "silo"
    st.rerun()
if local_btn:
    st.session_state["_home_mode"] = "local"
    # Clear any previous SILO station selection
    st.session_state.pop("we_station", None)
    st.rerun()

mode = st.session_state.get("_home_mode")

if mode is None:
    st.info("Choose a climate data source above to begin.")
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
# LOCAL P51 MODE
# ══════════════════════════════════════════════════════════════════════════════
if mode == "local":
    st.markdown("---")
    st.subheader("📂 Select local P51 climate file")
    st.caption(f"Scanning: `{CLIM_DIR}`")

    p51_files = _scan_p51()

    if not p51_files:
        st.error(
            f"No .P51 files found in `Climate files/`.\n\n"
            f"Download P51 files from https://www.longpaddock.qld.gov.au/silo/ "
            f"and save them in the `Climate files/` folder inside your Watbal2026 folder."
        )
        st.stop()

    # Show files with date range preview
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

    file_labels = []
    file_infos  = []
    for f in p51_files:
        info = _peek(str(f))
        file_infos.append(info)
        if "error" not in info:
            file_labels.append(
                f"{f.stem}  ·  {info['start'][:4]}–{info['end'][:4]}"
                f"  ({info['years']} yr)  ·  ~{info['rain']:.0f} mm/yr"
            )
        else:
            file_labels.append(f"{f.stem}  ·  (could not read: {info['error'][:40]})")

    sel_idx = st.selectbox(
        "P51 file",
        range(len(p51_files)),
        format_func=lambda i: file_labels[i],
    )
    chosen_file = p51_files[sel_idx]
    chosen_info = file_infos[sel_idx]

    if "error" in chosen_info:
        st.error(f"Cannot read this file: {chosen_info['error']}")
        st.stop()

    st.success(
        f"**{chosen_file.stem}**  ·  "
        f"{chosen_info['start']} → {chosen_info['end']}  "
        f"·  {chosen_info['years']} years  "
        f"·  ~{chosen_info['rain']:.0f} mm/yr mean annual rain"
    )

    # Store in session and go to Run simulation
    if st.button("✅  Use this file → Go to Run simulation",
                 type="primary", width="stretch"):
        st.session_state["climate_source"] = "local"
        st.session_state["climate_p51_path"] = str(chosen_file)
        st.session_state["climate_p51_info"] = chosen_info
        st.session_state["climate_label"]    = chosen_file.stem
        st.session_state.pop("we_station", None)
        st.switch_page("pages/1_Run_simulation.py")

# ══════════════════════════════════════════════════════════════════════════════
# SILO MODE — map + search
# ══════════════════════════════════════════════════════════════════════════════
elif mode == "silo":
    st.markdown("---")

    # Already have a station selected?
    station = st.session_state.get("we_station")
    if station:
        rel_df = _load_rel()
        pct = None
        if rel_df is not None:
            try:
                pct = float(rel_df.loc[int(station["id"]), "pct_observed"])
            except Exception:
                pass
        col_c = reliability_color(pct)
        lbl_r = reliability_label(pct)

        st.success(
            f"✅ **{station['name']}**  [{station.get('state','')}]  "
            f"({station['lat']:.3f}, {station['lon']:.3f})  ·  {lbl_r}"
        )
        c1, c2 = st.columns(2)
        with c1:
            if st.button("✅  Use this station → Go to Run simulation",
                         type="primary", width="stretch"):
                st.session_state["climate_source"] = "silo"
                st.session_state["climate_label"]  = station["name"]
                st.switch_page("pages/1_Run_simulation.py")
        with c2:
            if st.button("🔄  Change station", width="stretch"):
                st.session_state.pop("we_station", None)
                st.rerun()
        st.markdown("---")

    # ── Station search ────────────────────────────────────────────────────────
    st.subheader("🔍 Search for a SILO station")
    q = st.text_input("Station name", placeholder="e.g.  Dalby  or  Goondiwindi")
    if st.button("Search SILO", type="primary") and q.strip():
        with st.spinner(f"Searching for '{q}'…"):
            try:
                results = search_stations(q.strip())
                st.session_state["_silo_results"] = results
            except Exception as e:
                st.error(f"Search failed: {e}")
                st.session_state["_silo_results"] = []

    results = st.session_state.get("_silo_results", [])
    if results:
        rel_df = _load_rel()
        st.markdown(f"**{len(results)} station(s) found — click to select:**")
        for s in results[:20]:
            pct = None
            if rel_df is not None:
                try:
                    pct = float(rel_df.loc[int(s["id"]), "pct_observed"])
                except Exception:
                    pass
            col_c = reliability_color(pct)
            lbl_r = reliability_label(pct)
            label = (f"📌  {s['name']}  [{s['state']}]  "
                     f"({s['lat']:.3f}, {s['lon']:.3f})  ·  {lbl_r}")
            if st.button(label, key=f"pick_{s['id']}", width="stretch"):
                set_station(s)
                st.session_state["_silo_results"] = []
                st.rerun()

    # ── Reliability map ───────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🗺️ Station map — click search result above to select")

    rel_df = _load_rel()
    if rel_df is None:
        st.warning("Reliability data not found.")
        st.stop()

    map_data = rel_df.reset_index().copy()
    map_data = map_data[
        map_data["lat"].notna() & map_data["lon"].notna() &
        (map_data["lat"] > -45) & (map_data["lat"] < -10) &
        (map_data["lon"] > 110) & (map_data["lon"] < 155)
    ].copy()

    # ── Map filters ───────────────────────────────────────────────────────────
    f1, f2, f3 = st.columns([2, 2, 2])
    with f1:
        states = ["All"] + sorted(rel_df["state"].dropna().unique().tolist())
        filter_state = st.selectbox("Filter by state", states, key="map_state")
    with f2:
        min_pct = st.slider("Min. % observed", 0, 100, 0, step=10, key="map_pct")
    with f3:
        # Apply filters before showing count
        filtered = map_data.copy()
        if filter_state != "All":
            filtered = filtered[filtered["state"] == filter_state]
        filtered = filtered[filtered["pct_observed"] >= min_pct]
        st.metric("Stations shown", len(filtered))

    map_data = filtered

    def _hex_rgb(h):
        h = h.lstrip("#")
        return [int(h[i:i+2], 16) for i in (0, 2, 4)] + [200]

    map_data = map_data.copy()
    map_data["color_rgb"] = map_data["pct_observed"].apply(
        lambda p: _hex_rgb(reliability_color(p))
    )
    map_data["tooltip"] = map_data.apply(
        lambda r: f"{r['name']} (#{r['station_id']})  {r['pct_observed']:.0f}% observed",
        axis=1,
    )

    try:
        import pydeck as pdk
        import pandas as pd

        highlight = []
        sel = st.session_state.get("we_station")
        if sel:
            highlight = [{"lat": sel["lat"], "lon": sel["lon"],
                          "tooltip": sel["name"], "color_rgb": [255,215,0,255]}]

        layers = [pdk.Layer(
            "ScatterplotLayer",
            data=map_data[["lat","lon","color_rgb","tooltip"]],
            get_position=["lon","lat"],
            get_fill_color="color_rgb",
            get_radius=5000,
            pickable=True,
            auto_highlight=True,
        )]
        if highlight:
            import pandas as _pd
            layers.append(pdk.Layer(
                "ScatterplotLayer",
                data=_pd.DataFrame(highlight),
                get_position=["lon","lat"],
                get_fill_color="color_rgb",
                get_radius=14000,
                pickable=True,
            ))

        st.pydeck_chart(pdk.Deck(
            layers=layers,
            initial_view_state=pdk.ViewState(
                latitude=-27.0, longitude=134.0, zoom=4, pitch=0,
            ),
            tooltip={"text": "{tooltip}"},
            map_style="light",
        ))

    except ImportError:
        import pandas as pd
        st.map(
            map_data.rename(columns={"lat":"latitude","lon":"longitude"}),
            size=5,
        )

    st.caption("💡 Colours: 🟢 ≥90% observed  🟡 50–89%  🔴 <50%  ⚫ no data  "
               "·  Use search above to select a station.")

    # ── Browse table ──────────────────────────────────────────────────────────
    with st.expander("📋 Browse stations table"):
        import pandas as pd
        show_cols = ["station_id","name","state","lat","lon",
                     "pct_observed","first_date","last_date"]
        disp = map_data[[c for c in show_cols if c in map_data.columns]].copy()
        disp = disp.sort_values("pct_observed", ascending=False)
        disp.columns = [c.replace("_"," ").title() for c in disp.columns]

        sel_tbl = st.dataframe(
            disp,
            use_container_width=True,
            height=280,
            on_select="rerun",
            selection_mode="single-row",
            hide_index=True,
        )
        if sel_tbl and sel_tbl.selection.rows:
            row = disp.iloc[sel_tbl.selection.rows[0]]
            orig = map_data.iloc[sel_tbl.selection.rows[0]]
            s_info = {
                "id":    int(orig["station_id"]),
                "name":  orig["name"],
                "label": f"{orig['name']}  [{orig['state']}]",
                "lat":   float(orig["lat"]),
                "lon":   float(orig["lon"]),
                "state": orig["state"],
            }
            set_station(s_info)
            st.rerun()
