"""
pages/5_Observed_vs_predicted.py
================================
Compare any two variables: observed vs predicted, two model outputs, or two
observed series. Time series, cumulative plot, scatter and simple statistics.

Observed data: data/three_site_events.csv (three contour-bay sites) by default,
or any uploaded event table with a date column.
Predicted data: the current Daily diagnostics run, a saved daily CSV in
results/, or an uploaded daily CSV.
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

st.set_page_config(page_title="Observed vs predicted", page_icon="⚖️", layout="wide")
st.markdown("""<style>
h1,h2,h3{white-space:nowrap!important;}
.block-container{padding-top:1rem;}
</style>""", unsafe_allow_html=True)

from core.styles import apply_styles
from core.compare import (
    load_observed, observed_variables, normalise_predicted, predicted_variables,
    observed_series, predicted_series, pair, stats, cumulative,
    guess_site_treatment, OBS_TO_PRED, DEFAULT_OFFSET,
)
apply_styles()

ROOT        = Path(__file__).parent.parent
DATA_DIR    = ROOT / "data"
RESULTS_DIR = ROOT / "results"
OBS_DEFAULT = DATA_DIR / "three_site_events.csv"

C_X, C_Y   = "#2a78d6", "#eb6834"   # series colours (blue, orange)
C_RAIN     = "rgba(120,140,160,0.55)"
C_REF      = "#8a8986"

st.title("⚖️ Observed vs predicted")
st.caption("Compare any two variables — observed vs predicted, two model outputs, "
           "or two observed series — as a time series, cumulative totals, a scatter "
           "and simple statistics.")

# ── Data sources ──────────────────────────────────────────────────────────────
with st.expander("📂 Data sources", expanded=False):
    u1, u2 = st.columns(2)
    with u1:
        obs_up = st.file_uploader("Observed event file (optional, CSV)", type=["csv"],
                                  key="ovp_obs_up",
                                  help="Needs a date column. site, treatment and bay "
                                       "columns are used if present. Default is the "
                                       "three-site contour-bay event table.")
    with u2:
        pred_ups = st.file_uploader("Predicted daily output (optional, CSV)",
                                    type=["csv"], accept_multiple_files=True,
                                    key="ovp_pred_up",
                                    help="A Daily diagnostics CSV export, or any daily "
                                         "CSV with a date column.")


@st.cache_data(show_spinner=False)
def _obs_from_path(path: str, mtime: float):
    return load_observed(path)


@st.cache_data(show_spinner=False)
def _obs_from_bytes(data: bytes):
    import io
    return load_observed(io.BytesIO(data))


@st.cache_data(show_spinner=False)
def _pred_from_path(path: str, mtime: float):
    return normalise_predicted(pd.read_csv(path))


@st.cache_data(show_spinner=False)
def _pred_from_bytes(data: bytes):
    import io
    return normalise_predicted(pd.read_csv(io.BytesIO(data)))


# Observed
obs_df, obs_name = None, None
try:
    if obs_up is not None:
        obs_df, obs_name = _obs_from_bytes(obs_up.getvalue()), obs_up.name
    elif OBS_DEFAULT.exists():
        obs_df = _obs_from_path(str(OBS_DEFAULT), OBS_DEFAULT.stat().st_mtime)
        obs_name = OBS_DEFAULT.name
except Exception as e:
    st.error(f"Could not read observed file: {e}")

# Predicted sources: label -> frame
pred_sources = {}
_run_vege = ""
if st.session_state.get("_dc_df") is not None:
    _run_vege = Path(st.session_state.get("_last_vege_path", "")).stem
    _run_soil = Path(st.session_state.get("_last_soil_path", "")).stem
    _lab = f"Current run: {_run_vege or 'Daily diagnostics'}"
    pred_sources[_lab] = normalise_predicted(st.session_state["_dc_df"])
for f in (pred_ups or []):
    try:
        pred_sources[f"Upload: {f.name}"] = _pred_from_bytes(f.getvalue())
    except Exception as e:
        st.warning(f"Skipped {f.name}: {e}")
if RESULTS_DIR.exists():
    for f in sorted(RESULTS_DIR.glob("*_daily*.csv"),
                    key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
        try:
            pred_sources[f"Saved: {f.stem}"] = _pred_from_path(str(f), f.stat().st_mtime)
        except Exception:
            pass

if obs_df is None and not pred_sources:
    st.warning("No observed file and no model output found.")
    st.page_link("pages/3_Daily_diagnostics.py", label="→ Run the model in Daily diagnostics")
    st.stop()

sources = (["Observed"] if obs_df is not None else []) + list(pred_sources)

if not pred_sources:
    st.info("No model output yet — you can compare observed series now. For O vs P, "
            "run the model in **Daily diagnostics** (it saves a daily CSV this page "
            "picks up) or upload a daily CSV under Data sources.")

st_map = (obs_df.groupby("site")["treatment"].unique().apply(sorted).to_dict()
          if obs_df is not None else {})
g_site, g_trt = guess_site_treatment(_run_vege or (list(pred_sources)[0] if pred_sources else ""),
                                     st_map)


# ── Variable pickers ──────────────────────────────────────────────────────────
def picker(side: str, default_source: str, default_obs_col: str = None,
           default_pred: str = None):
    k = f"ovp_{side}"
    src = st.selectbox("Source", sources,
                       index=sources.index(default_source) if default_source in sources else 0,
                       key=f"{k}_src")
    if src == "Observed":
        sites = sorted(st_map)
        s_def = g_site if g_site in sites else sites[0]
        site = st.selectbox("Site", sites, index=sites.index(s_def), key=f"{k}_site")
        trts = st_map[site]
        t_def = g_trt if (g_trt in trts and site == g_site) else trts[0]
        trt = st.selectbox("Treatment", trts, index=trts.index(t_def), key=f"{k}_trt")
        sub = obs_df[(obs_df["site"] == site) & (obs_df["treatment"] == trt)]
        bays = sorted(sub["bay"].dropna().unique().tolist())
        bay = "All bays"
        if len(bays) > 1:
            bay = st.selectbox("Bay", ["All bays"] + bays, key=f"{k}_bay",
                               format_func=lambda b: b if b == "All bays" else f"Bay {int(b)}",
                               help="Treatments rotate between bays; 'All bays' "
                                    "averages bays recorded on the same date.")
        vars_ = observed_variables(sub)
        labels = list(vars_)
        v_def = next((l for l, c in vars_.items() if c == default_obs_col), labels[0])
        var = st.selectbox("Variable", labels, index=labels.index(v_def), key=f"{k}_var")
        tag = f"Obs {site} {trt}" + ("" if bay == "All bays" else f" bay {int(bay)}")
        return observed_series(obs_df, site, trt, bay, vars_[var], f"{tag}: {var}")
    df = pred_sources[src]
    vars_ = predicted_variables(df)
    labels = list(vars_)
    v_def = next((l for l in labels if default_pred and l.startswith(default_pred)), labels[0])
    var = st.selectbox("Variable", labels, index=labels.index(v_def), key=f"{k}_var")
    tag = "Pred" + (f" ({src.split(': ',1)[-1]})" if len(pred_sources) > 1 else "")
    return predicted_series(df, vars_[var], f"{tag}: {var}")


first_pred = next(iter(pred_sources), None)
cx, cy = st.columns(2)
with cx:
    with st.container(border=True):
        st.markdown("**X — reference** (usually observed)")
        sx = picker("x", "Observed" if obs_df is not None else first_pred,
                    default_obs_col="runoff_mm", default_pred="Runoff")
with cy:
    with st.container(border=True):
        st.markdown("**Y — compared** (usually predicted)")
        _xcol = next((c for l, c in observed_variables(obs_df).items()
                      if sx.name.endswith(l)), None) if obs_df is not None else None
        sy = picker("y", first_pred or "Observed",
                    default_obs_col="soil_movement_t_ha",
                    default_pred=OBS_TO_PRED.get(_xcol, "Runoff"))

mode = {("obs", "pred"): "O vs P", ("pred", "obs"): "O vs P",
        ("pred", "pred"): "P vs P", ("obs", "obs"): "O vs O"}[(sx.kind, sy.kind)]
obs_side = sx if sx.kind == "obs" else (sy if sy.kind == "obs" else None)

# ── Matching and period ───────────────────────────────────────────────────────
spans = [s.values.dropna().index for s in (sx, sy) if len(s.values.dropna())]
if not spans:
    st.warning("No data for that selection.")
    st.stop()
lo = max(i.min() for i in spans).date()
hi = min(i.max() for i in spans).date()
if lo > hi:
    st.error("The two series do not overlap in time — check the model run covers "
             "the observation period.")
    st.stop()

with st.expander(f"⚙️ Matching and period  ·  {mode}", expanded=False):
    m1, m2, m3 = st.columns(3)
    with m1:
        period = st.date_input("Period", value=(lo, hi), key=f"ovp_period_{lo}_{hi}")
        start, end = (period if isinstance(period, (list, tuple)) and len(period) == 2
                      else (lo, hi))
    offset, window, incl, nonzero = 0, 0, False, True
    with m2:
        if mode == "O vs P":
            offset = st.number_input(
                "Model day offset", -3, 3, DEFAULT_OFFSET.get(obs_side.site, 0), 1,
                key=f"ovp_off_{obs_side.site}",
                help="Model value is taken from event date + offset. Greenwood and "
                     "Wallumbilla event dates are storm days; the climate file books "
                     "that rain to the next 9 am reading, so the default is +1.")
            window = st.radio("Window", [0, 1], horizontal=True, key="ovp_win",
                              format_func=lambda w: "Same day" if w == 0 else "± 1 day",
                              help="± 1 day sums model runoff/erosion over 3 days, each "
                                   "model day credited to the nearest event only.")
    with m3:
        if mode == "O vs P":
            incl = st.checkbox("Add model-only events (observed = 0)", False,
                               key="ovp_incl",
                               help="Model days with runoff/erosion that match no "
                                    "observed event are added as pairs with observed 0.")
        elif mode == "P vs P":
            nonzero = st.checkbox("Only days where either value > 0", True,
                                  key="ovp_nz")
        else:
            incl = st.checkbox("Missing event = 0", False, key="ovp_incl_oo",
                               help="Pair dates recorded for only one series with 0.")
    c1, c2 = st.columns(2)
    with c1:
        cum_basis = st.radio("Cumulative totals from",
                             ["All records in period", "Paired values only"],
                             horizontal=True, key="ovp_cum",
                             help="'All records' totals every model day and every "
                                  "observed event in the period (the P:O used in "
                                  "calibration). 'Paired' totals only matched pairs.")
    with c2:
        log_axes = st.checkbox("Log axes on scatter", False, key="ovp_log")

pairs = pair(sx, sy, offset=offset, window=window, include_unmatched=incl,
             nonzero_only=nonzero, start=start, end=end)
S = stats(pairs)

# ── Statistics ────────────────────────────────────────────────────────────────
def _f(v, d=2):
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:,.{d}f}"


both_add = sx.additive and sy.additive
cum_x = cum_y = None
if both_add:
    if cum_basis.startswith("All"):
        cum_x = cumulative(sx, start, end, offset if sx.kind == "pred" and mode == "O vs P" else 0)
        cum_y = cumulative(sy, start, end, offset if sy.kind == "pred" and mode == "O vs P" else 0)
    else:
        cum_x = pairs.set_index("date")["x"].cumsum()
        cum_y = pairs.set_index("date")["y"].cumsum()

ratio_lab = "P:O" if mode == "O vs P" else "Y:X"
if mode == "O vs P" and sx.kind == "pred":
    ratio_lab = "O:P (Y:X)"

st.divider()
if S["n"] == 0:
    st.warning("No pairs for this selection and period.")
    st.stop()

tot_ratio = (cum_y.iloc[-1] / cum_x.iloc[-1]
             if cum_x is not None and len(cum_x) and len(cum_y) and cum_x.iloc[-1] != 0
             else S.get("ratio"))
k = st.columns(5)
k[0].metric("Pairs (n)", f"{S['n']}")
k[1].metric(f"{ratio_lab} totals", _f(tot_ratio),
            help="Ratio of cumulative totals (Cumulative basis setting). "
                 "Shown as the ratio of paired means for non-additive variables.")
k[2].metric("r²", _f(S.get("r2")))
k[3].metric("NSE", _f(S.get("nse")) if mode == "O vs P" else "—",
            help="Nash–Sutcliffe efficiency, X as reference. Shown for O vs P only.")
k[4].metric("RMSE", _f(S.get("rmse"), 3))

with st.expander("Statistics table", expanded=False):
    rows = [
        ("Pairs (n)", S["n"]),
        ("Mean X", S.get("mean_x")), ("Mean Y", S.get("mean_y")),
        ("Total X (pairs)", S.get("total_x")), ("Total Y (pairs)", S.get("total_y")),
        ("Y:X ratio (pairs)", S.get("ratio")),
        ("Y:X ratio (cumulative basis)", tot_ratio),
        ("Slope (Y on X)", S.get("slope")), ("Intercept", S.get("intercept")),
        ("r²", S.get("r2")), ("RMSE", S.get("rmse")),
        ("NSE (X as reference)", S.get("nse")),
    ]
    stats_df = pd.DataFrame({"Statistic": [r[0] for r in rows],
                             "Value": [_f(r[1], 3) if not isinstance(r[1], int) else str(r[1])
                                       for r in rows]})
    st.dataframe(stats_df, hide_index=True, width="stretch")
    st.caption(f"X = {sx.name}  ·  Y = {sy.name}  ·  {start} to {end}"
               + (f"  ·  offset {offset:+d} d, "
                  f"{'same day' if window == 0 else '± 1 day'}" if mode == "O vs P" else ""))

same_units = sx.unit == sy.unit


# ── Plot helpers ──────────────────────────────────────────────────────────────
def _trace(s, xs, ys, col, cumul=False):
    """Observed as markers (lines when cumulative), predicted as lines."""
    if s.kind == "obs" and not cumul:
        return go.Scatter(x=xs, y=ys, name=s.name, mode="markers",
                          marker=dict(color=col, size=8,
                                      line=dict(color="white", width=1)),
                          hovertemplate="%{x|%d %b %Y}<br>%{y:.3g}<extra>%{fullData.name}</extra>")
    shape = "hv" if cumul else "linear"
    return go.Scatter(x=xs, y=ys, name=s.name, mode="lines",
                      line=dict(color=col, width=2, shape=shape),
                      hovertemplate="%{x|%d %b %Y}<br>%{y:.3g}<extra>%{fullData.name}</extra>")


def _layout(fig, h):
    fig.update_layout(
        height=h, margin=dict(l=60, r=20, t=30, b=30),
        plot_bgcolor="white", paper_bgcolor="white", hovermode="x unified",
        legend=dict(orientation="h", x=0, y=1.08, font=dict(size=12)),
        bargap=0)
    fig.update_xaxes(showgrid=False, showline=True, linecolor="#ccc")
    fig.update_yaxes(gridcolor="rgba(0,0,0,0.06)", zeroline=False)
    return fig


def _rain_for_plot():
    for s in (sx, sy):
        if s.kind == "pred" and s.rain is not None:
            r = s.rain
            if mode == "O vs P" and offset:
                r = r.copy(); r.index = r.index - pd.Timedelta(days=offset)
            return r[str(start):str(end)], "Model rain (mm)"
    for s in (sx, sy):
        if s.rain is not None:
            return s.rain[str(start):str(end)], "Event rain (mm)"
    return None, None


def _ts_values(s):
    v = s.values.dropna()
    if s.kind == "pred" and mode == "O vs P" and offset:
        v = v.copy(); v.index = v.index - pd.Timedelta(days=offset)
    return v[str(start):str(end)]


def _unit_label(s):
    return s.name.split(": ", 1)[-1]


# ── Charts ────────────────────────────────────────────────────────────────────
tab_ts, tab_cum, tab_sc, tab_tbl = st.tabs(
    ["📈 Time series", "📊 Cumulative", "⚬ Scatter", "🗒 Paired data"])

_shift_note = (f"Model values plotted on event dates (model day {offset:+d} d)."
               if mode == "O vs P" and offset else "")

with tab_ts:
    rain, rain_lab = _rain_for_plot()
    nrows = 1 + (rain is not None) + (0 if same_units else 1)
    heights = ([0.22] if rain is not None else []) + \
              ([1.0] if same_units else [0.39, 0.39])
    fig = make_subplots(rows=nrows, cols=1, shared_xaxes=True,
                        vertical_spacing=0.04, row_heights=heights)
    r0 = 1
    if rain is not None:
        fig.add_trace(go.Bar(x=rain.index, y=rain.values, name=rain_lab,
                             marker_color=C_RAIN,
                             hovertemplate="%{x|%d %b %Y}<br>%{y:.1f} mm<extra>Rain</extra>"),
                      row=1, col=1)
        fig.update_yaxes(title_text="Rain mm", autorange="reversed", row=1, col=1)
        r0 = 2
    vx, vy = _ts_values(sx), _ts_values(sy)
    fig.add_trace(_trace(sx, vx.index, vx.values, C_X), row=r0, col=1)
    ry = r0 if same_units else r0 + 1
    fig.add_trace(_trace(sy, vy.index, vy.values, C_Y), row=ry, col=1)
    if same_units:
        fig.update_yaxes(title_text=_unit_label(sx), rangemode="tozero", row=r0, col=1)
    else:
        fig.update_yaxes(title_text=_unit_label(sx), rangemode="tozero", row=r0, col=1)
        fig.update_yaxes(title_text=_unit_label(sy), rangemode="tozero", row=ry, col=1)
    _layout(fig, 520 if nrows > 2 else 460)
    st.plotly_chart(fig, width="stretch")
    st.caption("Observed events as dots, model as a line. " + _shift_note
               + ("" if same_units else " Different units, so each has its own panel."))

with tab_cum:
    if not both_add:
        st.info("Cumulative totals only make sense for additive variables "
                "(rain, runoff, soil movement, erosion …). Pick those to see this plot.")
    else:
        fig = make_subplots(rows=1 if same_units else 2, cols=1, shared_xaxes=True,
                            vertical_spacing=0.06)
        fig.add_trace(_trace(sx, cum_x.index, cum_x.values, C_X, cumul=True), row=1, col=1)
        fig.add_trace(_trace(sy, cum_y.index, cum_y.values, C_Y, cumul=True),
                      row=1 if same_units else 2, col=1)
        _ends = [c.iloc[-1] if len(c) else 0 for c in (cum_x, cum_y)]
        for i, (c, s, col) in enumerate([(cum_x, sx, C_X), (cum_y, sy, C_Y)]):
            if len(c):
                _up = (not same_units) or _ends[i] >= _ends[1 - i]
                fig.add_annotation(x=c.index[-1], y=c.iloc[-1],
                                   text=f"{c.iloc[-1]:,.1f}", showarrow=False,
                                   xanchor="left", xshift=6, yshift=8 if _up else -8,
                                   font=dict(size=12, color="#333"),
                                   row=1 if (same_units or i == 0) else 2, col=1)
        fig.update_yaxes(title_text=_unit_label(sx), rangemode="tozero", row=1, col=1)
        if not same_units:
            fig.update_yaxes(title_text=_unit_label(sy), rangemode="tozero", row=2, col=1)
        _layout(fig, 460)
        fig.update_layout(margin=dict(r=70))
        st.plotly_chart(fig, width="stretch")
        st.caption(f"{ratio_lab} of totals = **{_f(tot_ratio)}**  ·  basis: {cum_basis.lower()}"
                   + (f"  ·  {_shift_note}" if _shift_note else ""))

with tab_sc:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=pairs["x"], y=pairs["y"], mode="markers", name="Pairs",
        marker=dict(color=C_Y, size=9, opacity=0.8, line=dict(color="white", width=1)),
        customdata=pairs["date"].dt.strftime("%d %b %Y"),
        hovertemplate="%{customdata}<br>X %{x:.3g}<br>Y %{y:.3g}<extra></extra>"))
    pos = pairs[(pairs["x"] > 0) & (pairs["y"] > 0)] if log_axes else pairs
    if len(pos):
        m = max(pos["x"].max(), pos["y"].max())
        mn = min(pos["x"].min(), pos["y"].min()) if log_axes else 0
        if same_units:
            fig.add_trace(go.Scatter(x=[mn, m], y=[mn, m], mode="lines", name="1:1",
                                     line=dict(color=C_REF, width=1.5, dash="dash"),
                                     hoverinfo="skip"))
        if "slope" in S:
            xs = np.linspace(max(mn, pos["x"].min()), pos["x"].max(), 50)
            fig.add_trace(go.Scatter(
                x=xs, y=S["slope"] * xs + S["intercept"], mode="lines",
                name=f"Fit  y = {S['slope']:.2f}x {S['intercept']:+.2f}  (r² {S['r2']:.2f})",
                line=dict(color=C_X, width=2), hoverinfo="skip"))
    fig.update_layout(
        height=520, margin=dict(l=60, r=20, t=30, b=50),
        plot_bgcolor="white", paper_bgcolor="white",
        legend=dict(orientation="h", x=0, y=1.08, font=dict(size=12)),
        xaxis=dict(title=f"X: {sx.name}", gridcolor="rgba(0,0,0,0.06)",
                   type="log" if log_axes else "linear", zeroline=False),
        yaxis=dict(title=f"Y: {sy.name}", gridcolor="rgba(0,0,0,0.06)",
                   type="log" if log_axes else "linear", zeroline=False),
    )
    if same_units and not log_axes and len(pairs):
        _top = 1.05 * max(pairs["x"].max(), pairs["y"].max())
        _bot = min(0, pairs["x"].min(), pairs["y"].min())
        fig.update_xaxes(range=[_bot, _top])
        fig.update_yaxes(range=[_bot, _top])
    st.plotly_chart(fig, width="stretch")
    if log_axes:
        st.caption("Log axes hide zero values; statistics still use all pairs.")

with tab_tbl:
    show = pairs.rename(columns={"date": "Date", "x": f"X: {sx.name}", "y": f"Y: {sy.name}"})
    show["Date"] = show["Date"].dt.date
    st.dataframe(show, hide_index=True, width="stretch", height=380)

# ── Downloads ─────────────────────────────────────────────────────────────────
st.divider()
stamp = datetime.now().strftime("%Y%m%d")
safe = f"OvP_{sx.name}_vs_{sy.name}".replace(" ", "_").replace(":", "").replace("/", "-")
safe = "".join(ch for ch in safe if ch.isalnum() or ch in "_-.()%")[:90]
d1, d2 = st.columns(2)
with d1:
    st.download_button("⬇ Paired data (CSV)", show.to_csv(index=False).encode(),
                       f"{safe}_{stamp}_pairs.csv", "text/csv", width="stretch")
with d2:
    st.download_button("⬇ Statistics (CSV)", stats_df.to_csv(index=False).encode(),
                       f"{safe}_{stamp}_stats.csv", "text/csv", width="stretch")

if obs_name:
    st.caption(f"Observed file: `{obs_name}`")
