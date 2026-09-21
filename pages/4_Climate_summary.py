"""
pages/3_Climate_summary.py
============================
Long-term monthly climate averages for the selected SILO station —
rainfall, evaporation, and min/max temperature.
Borrowed and adapted from Weather Explorer.
"""

import sys, io
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
st.set_page_config(page_title="Climate summary", page_icon="📅", layout="wide")

from core.styles import apply_styles, load_station
from core.nav import HOME
from core.silo import ensure_climate_cached, SiloUnavailableError

apply_styles()

MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun",
               "Jul","Aug","Sep","Oct","Nov","Dec"]
_FULL_YEAR_DAYS = 360

st.title("📅 Monthly climate averages")
st.caption("Long-term monthly averages: rainfall, evaporation, min/max temperature.")

station      = load_station()
clim_source  = st.session_state.get("climate_source")
clim_label   = st.session_state.get("climate_label", "")

if clim_source == "silo" and station:
    with st.spinner(f"Loading climate data for {station['name']}…"):
        try:
            df = ensure_climate_cached(
                station["id"], lat=station["lat"], lon=station["lon"],
                session_state=st.session_state,
            )
        except SiloUnavailableError as e:
            st.error(f"SILO unavailable: {e}")
            st.stop()
        except Exception as e:
            st.error(f"Data fetch failed: {e}")
            st.stop()
    st.success(f"🌐 {station.get('label', station['name'])}")

elif clim_source == "local":
    p51_path = st.session_state.get("climate_p51_path")
    if not p51_path or not Path(p51_path).exists():
        st.warning("Local P51 file not found. Please re-select on Home page.")
        st.page_link("pages/1_Setup_scenarios.py", label="← Setup scenarios")
        st.stop()
    with st.spinner(f"Loading {clim_label}…"):
        try:
            from core.read_p51 import read_p51
            _, df = read_p51(Path(p51_path))
        except Exception as e:
            st.error(f"Could not read P51 file: {e}")
            st.stop()
    st.success(f"📂 {clim_label}")

else:
    st.info("No climate selected. Go to Setup scenarios to select a station or P51 file.")
    st.page_link("pages/1_Setup_scenarios.py", label="← Setup scenarios")
    st.stop()

start_year = int(df["year"].min())
end_year   = int(df["year"].max())
available  = sorted(df["year"].unique())

# Safe name for titles and filenames — works for both SILO and local P51
_site_name = (station["name"] if station else clim_label) or "Climate"
_site_slug = _site_name.replace(" ", "_")

# ── Monthly averages ──────────────────────────────────────────────────────────
monthly_rain = (df.groupby(["year","month"])["rain"].sum()
                  .groupby("month").mean()
                  .reindex(range(1,13), fill_value=0.0))
monthly_evap = (df.groupby(["year","month"])["epan"].sum()
                  .groupby("month").mean()
                  .reindex(range(1,13), fill_value=0.0))
monthly_tmax = df.groupby("month")["tmax"].mean().reindex(range(1,13))
monthly_tmin = df.groupby("month")["tmin"].mean().reindex(range(1,13))

days_per_year = df.groupby("year").size()
full_years    = days_per_year[days_per_year >= _FULL_YEAR_DAYS].index
annual_rain   = df[df["year"].isin(full_years)].groupby("year")["rain"].sum()
annual_evap   = df[df["year"].isin(full_years)].groupby("year")["epan"].sum()

# ── Plotly chart ──────────────────────────────────────────────────────────────
import plotly.graph_objects as go

fig = go.Figure()
fig.add_trace(go.Bar(
    x=MONTH_NAMES, y=monthly_rain.round(1), name="Rainfall",
    marker_color="rgba(26,82,118,0.75)", yaxis="y",
))
fig.add_trace(go.Scatter(
    x=MONTH_NAMES, y=monthly_evap.round(1), name="Evaporation",
    line=dict(color="rgba(52,152,219,0.95)", width=2.2),
    mode="lines+markers", marker=dict(size=5), yaxis="y",
))
fig.add_trace(go.Scatter(
    x=MONTH_NAMES, y=monthly_tmax.round(1), name="Max temp",
    line=dict(color="rgba(192,57,43,0.9)", width=2),
    mode="lines+markers", marker=dict(size=5), yaxis="y2",
))
fig.add_trace(go.Scatter(
    x=MONTH_NAMES, y=monthly_tmin.round(1), name="Min temp",
    line=dict(color="rgba(243,156,18,0.95)", width=2),
    mode="lines+markers", marker=dict(size=5), yaxis="y2",
))
fig.update_layout(
    title=dict(
        text=(f"Monthly average: {_site_name}, "
              f"{start_year}–{end_year}  "
              f"(Rain {monthly_rain.sum():.0f}mm  "
              f"Evap {monthly_evap.sum():.0f}mm/yr)"),
        x=0.5, xanchor="center", font=dict(size=15),
    ),
    height=450, margin=dict(l=55,r=55,t=55,b=70),
    legend=dict(orientation="h", x=0.5, xanchor="center", y=-0.16,
                font=dict(size=11)),
    plot_bgcolor="white", paper_bgcolor="white",
    hovermode="x unified", bargap=0.25,
    yaxis=dict(title="Rain / Evap (mm)", rangemode="tozero",
               gridcolor="rgba(0,0,0,0.06)", tickfont=dict(size=11)),
    yaxis2=dict(title="Temperature (°C)", overlaying="y", side="right",
                rangemode="tozero", showgrid=False, tickfont=dict(size=11)),
    xaxis=dict(showgrid=False, tickfont=dict(size=12)),
)
st.plotly_chart(fig, width="stretch")

# ── Annual summary ────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("Mean annual rain",  f"{annual_rain.mean():.0f} mm")
c2.metric("Mean annual evap",  f"{annual_evap.mean():.0f} mm")
c3.metric("Rain range",        f"{annual_rain.min():.0f}–{annual_rain.max():.0f} mm")
c4.metric("Record length",     f"{start_year}–{end_year}  ({len(full_years)} yr)")

# ── Yearly grid ───────────────────────────────────────────────────────────────
with st.expander("📅 Yearly rainfall grid (monthly totals)"):
    max_years    = min(30, len(available))
    default_yrs  = min(25, max_years)
    n_years      = st.slider("Years to show", min(5,max_years), max_years, default_yrs)
    recent_years = [y for y in available if y > end_year - n_years]

    grid = (df[df["year"].isin(recent_years)]
              .pivot_table(index="year", columns="month", values="rain", aggfunc="sum")
              .reindex(index=sorted(recent_years), columns=range(1,13), fill_value=0.0)
              .fillna(0.0))
    grid.columns = MONTH_NAMES
    grid.index.name = "Year"

    st.dataframe(
        grid.style.background_gradient(cmap="Blues", axis=None).format("{:.0f}"),
        use_container_width=True, height=min(600, 60+len(grid)*35),
    )
    st.download_button(
        "⬇ Download grid (CSV)",
        grid.to_csv().encode(),
        f"{_site_slug}_monthly_rain_grid.csv",
        "text/csv",
    )

# ── Downloads ─────────────────────────────────────────────────────────────────
csv_df = pd.DataFrame({
    "Month": MONTH_NAMES,
    "Rainfall_mm": monthly_rain.round(1).values,
    "Evaporation_mm": monthly_evap.round(1).values,
    "MaxTemp_C": monthly_tmax.round(1).values,
    "MinTemp_C": monthly_tmin.round(1).values,
})

def _build_jpeg() -> io.BytesIO:
    fig_m, ax1 = plt.subplots(figsize=(11,6), dpi=130)
    xi = range(12)
    ax1.bar(xi, monthly_rain.values, color="#1a5276", alpha=0.8, label="Rainfall", zorder=2)
    ax1.plot(xi, monthly_evap.values, color="#3498db", lw=2, marker="o", ms=4,
             label="Evaporation", zorder=3)
    ax1.set_ylabel("Rain/Evap (mm)", fontsize=10)
    ax1.set_ylim(bottom=0)
    ax1.set_xticks(list(xi)); ax1.set_xticklabels(MONTH_NAMES, fontsize=9)
    ax1.grid(axis="y", color="0.92", linewidth=0.7)
    ax1.spines[["top"]].set_visible(False)
    ax2 = ax1.twinx()
    ax2.plot(xi, monthly_tmax.values, color="#c0392b", lw=2, marker="o", ms=4, label="Max temp")
    ax2.plot(xi, monthly_tmin.values, color="#f39c12", lw=2, marker="o", ms=4, label="Min temp")
    ax2.set_ylabel("Temperature (°C)", fontsize=10)
    ax2.set_ylim(bottom=0); ax2.spines[["top"]].set_visible(False)
    fig_m.suptitle(
        f"Monthly average: {_site_name}, {start_year}–{end_year}",
        fontsize=14, y=0.98,
    )
    h1,l1 = ax1.get_legend_handles_labels()
    h2,l2 = ax2.get_legend_handles_labels()
    fig_m.legend(h1+h2, l1+l2, loc="lower center", ncol=4,
                 fontsize=9, frameon=False, bbox_to_anchor=(0.5,-0.02))
    fig_m.subplots_adjust(top=0.90, bottom=0.16)
    buf = io.BytesIO()
    fig_m.savefig(buf, format="jpeg", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig_m)
    buf.seek(0)
    return buf

st.divider()
d1, d2 = st.columns(2)
with d1:
    st.download_button("⬇ Monthly averages (CSV)",
                       csv_df.to_csv(index=False).encode(),
                       f"{_site_slug}_monthly_avg.csv",
                       "text/csv", width="stretch")
with d2:
    with st.spinner("Generating chart…"):
        jpeg_buf = _build_jpeg()
    st.download_button("🖼 Download chart (JPEG)", jpeg_buf,
                       f"{_site_slug}_monthly_avg.jpg",
                       "image/jpeg", width="stretch")

# ── P51 download ──────────────────────────────────────────────────────────────
def _build_p51(df: pd.DataFrame, site_name: str,
               station_id=None, lat=None, lon=None) -> bytes:
    """
    Reconstruct a SILO-style P51 text file from the in-memory DataFrame.
    Line 1 format expected by read_p51:  lat lon station_no NAME
    """
    sid  = station_id or 0
    _lat = lat  if lat  is not None else -25.0
    _lon = lon  if lon  is not None else 150.0
    lines = []
    # Line 1: lat lon station_id NAME  (what read_p51 expects)
    lines.append(f"{_lat:.4f} {_lon:.4f}  {sid}  {site_name.upper()}")
    lines.append("       date  jday  rain   evap   tmax   tmin")
    for _, row in df.iterrows():
        try:
            dt   = pd.Timestamp(year=int(row["year"]), month=int(row["month"]),
                                day=int(row["day"]))
            date = dt.strftime("%Y%m%d")
            jday = dt.timetuple().tm_yday
        except Exception:
            continue
        rain = float(row.get("rain", 0.0))
        evap = float(row.get("epan", row.get("evap", 0.0)))
        tmax = float(row.get("tmax", 0.0))
        tmin = float(row.get("tmin", 0.0))
        lines.append(f"{date:>11}  {jday:4d}  {rain:5.1f}  {evap:5.1f}  "
                     f"{tmax:5.1f}  {tmin:5.1f}")
    return "\n".join(lines).encode()

st.divider()
st.markdown("#### ⬇ Download P51 climate file")
st.caption("Saves the full daily SILO dataset as a local P51 file — "
           "use it as a 'Local P51' source on the Setup scenarios page.")

_p51_bytes = _build_p51(
    df,
    site_name  = _site_name,
    station_id = station["id"]  if station else None,
    lat        = station["lat"] if station else None,
    lon        = station["lon"] if station else None,
)
st.download_button(
    f"⬇ Download P51  ({_site_name}  {start_year}–{end_year})",
    _p51_bytes,
    f"{_site_slug}_{start_year}_{end_year}.p51",
    "text/plain",
    width="stretch",
)
