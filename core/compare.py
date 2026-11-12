"""
core/compare.py
===============
Pairing and statistics for the Observed vs predicted page.

A "series" is one variable from one source:
  * observed  - sparse event records (one value per event date), e.g. the
                three-site contour-bay event table;
  * predicted - a dense daily model trace (Watbal2026 daily output).

Any two series can be compared: O vs P, P vs P (two outputs or two runs) or
O vs O (two observed variables, or two treatments). Pairing rules:

  O vs P   predicted values are looked up on (event date + offset days).
           With a +/- window, additive predicted values (runoff, erosion ...)
           in the window are summed, each model day being credited to the
           nearest event only, so nothing is double counted.
  O vs O   paired on date.
  P vs P   paired day by day.

Ratio variables (concentrations) are rebuilt from their summed parts rather
than summed themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

MISSING_CODES = (-999, -99, -9999)

# ── Observed variables (column -> label, units, additive) ─────────────────────
OBS_VARS = {
    "rain_mm":                 ("Rain", "mm", True),
    "runoff_mm":               ("Runoff", "mm", True),
    "soil_movement_t_ha":      ("Soil movement", "t/ha", True),
    "suspended_sediment_t_ha": ("Suspended sediment", "t/ha", True),
    "rills_t_ha":              ("Rills", "t/ha", True),
    "fans_t_ha":               ("Fans", "t/ha", True),
    "total_conc_g_l":          ("Total sediment conc", "g/L", False),
    "suspended_conc_g_l":      ("Suspended conc", "g/L", False),
    "peak_runoff_mm_h":        ("Peak runoff rate", "mm/h", False),
    "cover_pct":               ("Cover", "%", False),
    "roughness_1_5":           ("Roughness", "1-5", False),
    "rain_prev10d_mm":         ("Rain previous 10 d", "mm", False),
    "i30_mm_h":                ("I30", "mm/h", False),
    "ei30":                    ("EI30", "", True),
}

# ── Predicted variables: label -> (candidate columns, units, additive) ────────
# Candidates cover both the raw model frame and the Daily diagnostics CSV.
PRED_VARS = {
    "Rain":                ((["rain", "Rain (mm)"]), "mm", True),
    "Runoff":              ((["runoff", "Runoff (mm)"]), "mm", True),
    "Erosion":             ((["sediment", "erosion", "Erosion (t/ha)"]), "t/ha", True),
    "Soil evap":           ((["soil_evap", "Soil evap (mm)"]), "mm", True),
    "Transpiration":       ((["transp", "Transp (mm)"]), "mm", True),
    "Drainage":            ((["drainage", "Drainage (mm)"]), "mm", True),
    "PAW":                 ((["pasw", "PAW (mm)"]), "mm", False),
    "SW total":            ((["sw_total", "SW total (mm)"]), "mm", False),
    "Green cover":         ((["green_cover", "Green cover %"]), "%", False),
    "Residue cover":       ((["residue_cover", "Residue cover %"]), "%", False),
    "Root depth":          ((["root_depth", "Root depth (mm)"]), "mm", False),
    "CN":                  ((["cn2_eff", "CN (cover+soil water)"]), "", False),
    "Yield":               ((["yield", "Yield (t/ha)"]), "t/ha", True),
}
# Derived ratio: concentration = 100 x erosion / runoff (t/ha per mm -> g/L)
PRED_RATIOS = {"Sediment conc": ("Erosion", "Runoff", 100.0, "g/L")}

# Matching between observed and predicted names, used for sensible defaults
OBS_TO_PRED = {
    "runoff_mm": "Runoff",
    "soil_movement_t_ha": "Erosion",
    "suspended_sediment_t_ha": "Erosion",
    "total_conc_g_l": "Sediment conc",
    "suspended_conc_g_l": "Sediment conc",
    "rain_mm": "Rain",
    "cover_pct": "Residue cover",
}

# Model rain is booked to the 9 am reading after the storm at these sites
DEFAULT_OFFSET = {"Greenwood": 1, "Wallumbilla": 1, "Greenmount": 0}


# ── Loading ───────────────────────────────────────────────────────────────────
def load_observed(src) -> pd.DataFrame:
    """Read an event table. Needs a date column; site/treatment/bay optional."""
    df = pd.read_csv(src)
    cols = {c.lower(): c for c in df.columns}
    if "date" not in cols:
        raise ValueError("Observed file needs a 'date' column.")
    df = df.rename(columns={cols["date"]: "date"})
    df["date"] = pd.to_datetime(df["date"], dayfirst=False, errors="coerce")
    df = df.dropna(subset=["date"])
    for c in ("site", "treatment"):
        if c not in df.columns:
            df[c] = "All"
        df[c] = df[c].fillna("All").astype(str)
    if "bay" not in df.columns:
        df["bay"] = np.nan
    num = df.select_dtypes("number").columns
    df[num] = df[num].mask(df[num].isin(MISSING_CODES))
    return df


def observed_variables(df: pd.DataFrame) -> dict:
    """label -> column, for numeric columns present (known ones first)."""
    out = {}
    for col, (lab, unit, _) in OBS_VARS.items():
        if col in df.columns and df[col].notna().any():
            out[f"{lab} ({unit})" if unit else lab] = col
    skip = {"bay", "slope_pct", "slope_length_m", "LS"} | set(out.values())
    for col in df.select_dtypes("number").columns:
        if col not in skip and df[col].notna().any():
            out[col] = col
    return out


def normalise_predicted(df: pd.DataFrame) -> pd.DataFrame:
    """Daily model output (raw frame or exported CSV) -> date-indexed frame."""
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        dcol = next((c for c in df.columns if c.lower() in ("date", "dates", "day")),
                    df.columns[0])
        df.index = pd.to_datetime(df[dcol], errors="coerce")
        df = df.drop(columns=[dcol])
    df = df[df.index.notna()].sort_index()
    df.index = df.index.normalize()
    df.index.name = "date"
    return df.apply(pd.to_numeric, errors="coerce")


def predicted_variables(df: pd.DataFrame) -> dict:
    """label -> column (or ratio key) available in a predicted frame."""
    out = {}
    for lab, (cands, unit, _) in PRED_VARS.items():
        col = next((c for c in cands if c in df.columns), None)
        if col is not None:
            out[f"{lab} ({unit})" if unit else lab] = col
    for lab, (num, den, _, unit) in PRED_RATIOS.items():
        if _pred_col(df, num) and _pred_col(df, den):
            out[f"{lab} ({unit})"] = f"ratio:{lab}"
    known = {c for cands, _, _ in PRED_VARS.values() for c in cands}
    for col in df.columns:
        if col not in known and col not in out.values() and df[col].notna().any():
            out[col] = col
    return out


def _pred_col(df, label):
    return next((c for c in PRED_VARS[label][0] if c in df.columns), None)


# ── Series ────────────────────────────────────────────────────────────────────
@dataclass
class Series:
    name: str                      # legend label, e.g. "Obs Runoff (mm)"
    kind: str                      # "obs" | "pred"
    values: pd.Series              # date-indexed
    additive: bool = True
    unit: str = ""
    site: Optional[str] = None
    treatment: Optional[str] = None
    parts: Optional[tuple] = None  # (num, den, factor) for ratio variables
    rain: Optional[pd.Series] = field(default=None, repr=False)


def observed_series(df, site, treatment, bay, col, label) -> Series:
    sub = df[(df["site"] == site) & (df["treatment"] == treatment)]
    if bay not in (None, "All bays"):
        sub = sub[sub["bay"] == bay]
    sub = sub.dropna(subset=[col])
    # several bays on one date -> mean
    vals = sub.groupby("date")[col].mean().sort_index()
    rain = (sub.groupby("date")["rain_mm"].mean().sort_index()
            if "rain_mm" in sub.columns else None)
    meta = OBS_VARS.get(col, (col, "", False))
    return Series(name=label, kind="obs", values=vals, additive=meta[2],
                  unit=meta[1], site=site, treatment=treatment, rain=rain)


def predicted_series(df, col_or_ratio, label) -> Series:
    rain_col = _pred_col(df, "Rain") if "Rain" in PRED_VARS else None
    rain = df[rain_col] if rain_col else None
    if str(col_or_ratio).startswith("ratio:"):
        key = col_or_ratio.split(":", 1)[1]
        num_l, den_l, fac, unit = PRED_RATIOS[key]
        num = df[_pred_col(df, num_l)].fillna(0)
        den = df[_pred_col(df, den_l)].fillna(0)
        vals = (fac * num / den).where(den > 0)
        return Series(name=label, kind="pred", values=vals, additive=False,
                      unit=unit, parts=(num, den, fac), rain=rain)
    additive, unit = False, ""
    for lab, (cands, u, add) in PRED_VARS.items():
        if col_or_ratio in cands:
            additive, unit = add, u
    return Series(name=label, kind="pred", values=df[col_or_ratio], additive=additive,
                  unit=unit, rain=rain)


# ── Pairing ───────────────────────────────────────────────────────────────────
def _sparse_vs_dense(obs: Series, pred: Series, offset: int, window: int,
                     include_unmatched: bool):
    """Return frame [date, obs, pred] keyed on observed event dates."""
    ev = obs.values
    if ev.empty:
        return pd.DataFrame(columns=["date", "obs", "pred"])
    tgt = ev.index + pd.Timedelta(days=offset)
    w = window if (pred.additive or pred.parts) else 0

    def comp(s):  # sum each component over its credited days
        s = s.fillna(0)
        if w == 0:
            got = s.reindex(tgt)
            return got.to_numpy(dtype=float), None
        lo, hi = tgt.min() - pd.Timedelta(days=w), tgt.max() + pd.Timedelta(days=w)
        s = s[lo:hi]
        tnum = tgt.values.astype("datetime64[D]").astype(np.int64)
        dnum = s.index.values.astype("datetime64[D]").astype(np.int64)
        idx = np.searchsorted(tnum, dnum)
        left = np.clip(idx - 1, 0, len(tnum) - 1)
        right = np.clip(idx, 0, len(tnum) - 1)
        dl, dr = np.abs(dnum - tnum[left]), np.abs(dnum - tnum[right])
        near = np.where(dl <= dr, left, right)
        dist = np.minimum(dl, dr)
        ok = dist <= w
        sums = np.zeros(len(tnum))
        np.add.at(sums, near[ok], s.to_numpy(dtype=float)[ok])
        covered = np.zeros(len(tnum), bool)
        covered[np.unique(near[ok])] = True
        # targets outside the model period stay NaN
        inside = (tgt >= pred.values.index.min()) & (tgt <= pred.values.index.max())
        sums[~np.asarray(inside)] = np.nan
        unmatched = s[~ok]
        return sums, unmatched

    if pred.parts:
        num, den, fac = pred.parts
        n, un_n = comp(num)
        d, _ = comp(den)
        with np.errstate(divide="ignore", invalid="ignore"):
            pv = np.where(d > 0, fac * n / d, np.nan)
        unmatched = None
    else:
        pv, unmatched = comp(pred.values)

    out = pd.DataFrame({"date": ev.index, "obs": ev.to_numpy(dtype=float), "pred": pv})

    if include_unmatched and pred.additive:
        pv_all = pred.values.fillna(0)
        lo = ev.index.min() + pd.Timedelta(days=offset)
        hi = ev.index.max() + pd.Timedelta(days=offset)
        pv_all = pv_all[lo:hi]
        if w == 0:
            extra = pv_all[(pv_all > 0) & ~pv_all.index.isin(tgt)]
        else:
            extra = unmatched[unmatched > 0] if unmatched is not None else pv_all.iloc[0:0]
        if len(extra):
            out = pd.concat([out, pd.DataFrame({
                "date": extra.index - pd.Timedelta(days=offset),
                "obs": 0.0, "pred": extra.to_numpy(dtype=float)})])
    return out.sort_values("date").reset_index(drop=True)


def pair(x: Series, y: Series, offset: int = 0, window: int = 0,
         include_unmatched: bool = False, nonzero_only: bool = True,
         start=None, end=None) -> pd.DataFrame:
    """Paired frame with columns date, x, y (NaN pairs dropped)."""
    if x.kind == "obs" and y.kind == "pred":
        p = _sparse_vs_dense(x, y, offset, window, include_unmatched)
        p = p.rename(columns={"obs": "x", "pred": "y"})
    elif x.kind == "pred" and y.kind == "obs":
        p = _sparse_vs_dense(y, x, offset, window, include_unmatched)
        p = p.rename(columns={"obs": "y", "pred": "x"})
    else:
        how = "outer" if (include_unmatched and x.additive and y.additive
                          and x.kind == "obs") else "inner"
        p = pd.concat([x.values.rename("x"), y.values.rename("y")], axis=1, join=how)
        if how == "outer":
            p = p.fillna(0.0)
        p = p.rename_axis("date").reset_index()
        if x.kind == "pred" and nonzero_only:
            p = p[(p["x"].fillna(0) != 0) | (p["y"].fillna(0) != 0)]
    if start is not None:
        p = p[p["date"] >= pd.Timestamp(start)]
    if end is not None:
        p = p[p["date"] <= pd.Timestamp(end)]
    return p.dropna(subset=["x", "y"]).reset_index(drop=True)


# ── Statistics ────────────────────────────────────────────────────────────────
def stats(p: pd.DataFrame) -> dict:
    """Simple comparison statistics, x treated as the reference."""
    x, y = p["x"].to_numpy(float), p["y"].to_numpy(float)
    n = len(x)
    out = {"n": n}
    if n == 0:
        return out
    out.update({
        "mean_x": x.mean(), "mean_y": y.mean(),
        "total_x": x.sum(), "total_y": y.sum(),
        "ratio": y.sum() / x.sum() if x.sum() != 0 else np.nan,
        "rmse": float(np.sqrt(np.mean((y - x) ** 2))),
    })
    if n >= 3 and np.ptp(x) > 0:
        slope, intercept = np.polyfit(x, y, 1)
        r = np.corrcoef(x, y)[0, 1] if np.ptp(y) > 0 else np.nan
        sst = np.sum((x - x.mean()) ** 2)
        out.update({"slope": slope, "intercept": intercept, "r2": r * r,
                    "nse": 1 - np.sum((y - x) ** 2) / sst if sst > 0 else np.nan})
    return out


def cumulative(s: Series, start=None, end=None, offset: int = 0) -> pd.Series:
    """Running total of a series over its own records within the period.
    Predicted dates are shifted back by offset so they line up with events."""
    v = s.values.dropna()
    if s.kind == "pred" and offset:
        v.index = v.index - pd.Timedelta(days=offset)
    if start is not None:
        v = v[v.index >= pd.Timestamp(start)]
    if end is not None:
        v = v[v.index <= pd.Timestamp(end)]
    return v.cumsum()


def guess_site_treatment(name: str, sites_treatments: dict):
    """Guess (site, treatment) from a vege/run file name, for defaults only."""
    n = (name or "").lower()
    site = next((s for s in sites_treatments if s.lower() in n), None)
    if not site:
        return None, None
    keys = {"burnt": "Burnt", "incorporat": "Incorporated", "mulch": "Mulch",
            "zero": "Zero till", "chisel": "Tilled", "scarif": "Tilled",
            "blade": "Tilled", "minimum": "Min till", "min_till": "Min till",
            "pasture": "Pasture"}
    for k, t in keys.items():
        if k in n and t in sites_treatments[site]:
            return site, t
    return site, None
