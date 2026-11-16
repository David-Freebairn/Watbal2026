"""
core/soil_excel.py
==================
Reader for Excel-format soil description files.

Expected layout (sheet "Soil description"):
  Row 0:  Soil name, <name>
  Row 1:  Parameter header
  Row 2:  Number of Horizons, N
  Row 3:  Layer Depth (Cumulative), v1, v2, ..., vN, mm
  Row 4:  Air dry moisture (AD),    v1, v2, ..., vN, %Vol
  Row 5:  Wilting point (WP),       v1, v2, ..., vN, %Vol
  Row 6:  Field capacity (FC),      v1, v2, ..., vN, %Vol
  Row 7:  Sat. water content (Sat), v1, v2, ..., vN, %Vol
  Row 8:  Max. drainage from layer, v1, v2, ..., vN, mm/day
  Row 9:  Bulk density,             v1, v2, ..., vN, g/cm3
  Row 10: PAWC per layer (computed — ignored)
  Row 11: Stage 1 evap. (U),        value, mm
  Row 12: Stage 2 evap. (Cona),     value
  Row 13: Runoff Curve Number (CN), value
  Row 14: CN reduction cover,       value
  Row 15: Erodibility (K),          value
  Row 16: Field Slope (S),          value, %
  Row 17: Slope Length (L),         value, m
  Row 18: Practice factor (P),      value
  Row 19: CN Reduction - Tillage,   value  (CN units, maximum just after tillage)
  Row 20: Rainfall to 0 roughness,  value  (mm of rain to remove the tillage effect)
  Row 21: Sediment Delivery Ratio,  value
  Row 22: Rill/interrill ratio,     value

Optional rows (matched by label, any position; leave out for the defaults):
  AMC mode,                         howleaky | capped   (default capped)
  Sediment cover coefficient,       b in conc = 16.52*exp(-b*cover%)  (default: PERFECT curve)
  Sediment winter fraction,         0-1  (default 1 = no season effect)
  Cover effect winter fraction,     0-1  (default 1)
  Storm season peak DOY,            day of year (default 15)

The profile returned is the same SoilProfile class that soil_xml.read_soil_xml
returns, with the same attribute names, so an Excel soil and the equivalent
.soil file give identical simulations.
"""

from __future__ import annotations
import pandas as pd
from pathlib import Path
try:
    from .soil import SoilProfile, SoilLayer
except ImportError:
    from core.soil import SoilProfile, SoilLayer


def _get_row(df: pd.DataFrame, *keywords) -> pd.Series | None:
    """Find the first row whose first cell contains all keywords (case-insensitive)."""
    kws = [k.lower() for k in keywords]
    for _, row in df.iterrows():
        cell = str(row.iloc[0]).lower()
        if all(k in cell for k in kws):
            return row
    return None


def _row_values(row: pd.Series, n: int) -> list[float]:
    """Extract n numeric values from a row (skipping label in col 0 and units at end)."""
    vals = []
    for v in row.iloc[1:]:
        try:
            f = float(v)
            if f == f:   # NaN check
                vals.append(f)
        except (ValueError, TypeError):
            pass
        if len(vals) == n:
            break
    return vals


def _scalar(row: pd.Series | None, default: float = 0.0) -> float:
    if row is None:
        return default
    vs = _row_values(row, 1)
    return vs[0] if vs else default


def _text(row: pd.Series | None) -> str | None:
    """First non-empty text value after the label."""
    if row is None:
        return None
    for v in row.iloc[1:]:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def read_soil_excel(path: str | Path) -> SoilProfile:
    """Read an Excel soil description file into the same SoilProfile as read_soil_xml."""
    path = Path(path)

    xl = pd.ExcelFile(path)
    sheet = xl.sheet_names[0]
    for s in xl.sheet_names:
        if 'soil' in s.lower() or 'descr' in s.lower():
            sheet = s; break
    df = pd.read_excel(path, sheet_name=sheet, header=None)

    # ── Soil name (cell B1 next to "Soil name"; file name if blank) ─────────
    name = path.stem
    for _, row in df.iterrows():
        cell = str(row.iloc[0]).lower()
        if 'soil name' in cell or 'name' == cell.strip():
            v = row.iloc[1] if len(row) > 1 else None
            if isinstance(v, str) and v.strip():
                name = v.strip()
            break

    # ── Layers ────────────────────────────────────────────────────────────────
    n = int(_scalar(_get_row(df, 'number', 'horizon'), 4))
    _dep_row = _get_row(df, 'layer', 'depth', 'cumul')
    if _dep_row is None: _dep_row = _get_row(df, 'layer depth')
    depths  = _row_values(_dep_row, n)
    ad_pct  = _row_values(_get_row(df, 'air', 'dry'), n)
    ll_pct  = _row_values(_get_row(df, 'wilting'), n)
    _dul_row = _get_row(df, 'field', 'capacity')
    if _dul_row is None: _dul_row = _get_row(df, 'field cap')
    dul_pct = _row_values(_dul_row, n)
    sat_pct = _row_values(_get_row(df, 'sat'), n)
    _dr_row = _get_row(df, 'drainage', 'layer')
    if _dr_row is None: _dr_row = _get_row(df, 'max', 'drain')
    drain_d = _row_values(_dr_row, n)
    bd      = _row_values(_get_row(df, 'bulk', 'density'), n)

    if not depths:  depths  = [300*i for i in range(1, n+1)]
    if not ad_pct:  ad_pct  = [10.0]*n
    if not ll_pct:  ll_pct  = [30.0]*n
    if not dul_pct: dul_pct = [50.0]*n
    if not sat_pct: sat_pct = [60.0]*n
    if not drain_d: drain_d = [50.0]*n
    if not bd:      bd      = [1.4]*n
    for lst in [depths, ad_pct, ll_pct, dul_pct, sat_pct, drain_d, bd]:
        while len(lst) < n: lst.append(lst[-1])

    layers, prev = [], 0.0
    for i in range(n):
        thick = depths[i] - prev
        ad, l, d, s_ = ad_pct[i] / 100, ll_pct[i] / 100, dul_pct[i] / 100, sat_pct[i] / 100
        layers.append(SoilLayer(
            depth_mm=depths[i], thickness=thick, airdry=ad, ll=l, dul=d, sat=s_,
            ksat=drain_d[i] / 24.0,                       # mm/day -> mm/hr, as read_soil_xml
            ll_mm=l * thick, dul_mm=d * thick, sat_mm=s_ * thick, airdry_mm=ad * thick,
            pawc=(d - l) * thick, bulk_density=bd[i]))
        prev = depths[i]

    # ── Scalars ───────────────────────────────────────────────────────────────
    u = _scalar(_get_row(df, 'stage 1', 'evap'), 4.0)
    _cona_row = _get_row(df, 'stage 2', 'evap')
    if _cona_row is None: _cona_row = _get_row(df, 'cona')
    cona = _scalar(_cona_row, 4.0)
    _cn_row = _get_row(df, 'runoff curve')
    if _cn_row is None: _cn_row = _get_row(df, 'curve number')
    cn2 = _scalar(_cn_row, 83.0)
    cn_red = _scalar(_get_row(df, 'cn reduction', 'cover'), 15.0)
    k = _scalar(_get_row(df, 'erodib'), 0.35)
    _slope_row = _get_row(df, 'slope', '%')
    if _slope_row is None: _slope_row = _get_row(df, 'field slope')
    slope = _scalar(_slope_row, 7.0)
    length = _scalar(_get_row(df, 'slope', 'length'), 60.0)
    _p_row = _get_row(df, 'practice', 'factor')
    if _p_row is None: _p_row = _get_row(df, 'p factor')
    p_fac = _scalar(_p_row, 1.0)
    rill = _scalar(_get_row(df, 'rill'), 1.0)
    till_cn = _scalar(_get_row(df, 'cn', 'till'), 0.0)
    rain_r = _scalar(_get_row(df, 'rainfall', 'rough'), 0.0)
    sdr = _scalar(_get_row(df, 'sediment', 'deliv'), 1.0)

    profile = SoilProfile(
        name=name, layers=layers, cona=cona, u=u, cn2_bare=cn2,
        cn_cover_reduction=cn_red,
        cn_tillage_max=till_cn,            # engine name (was tillage_cn_reduction: never used)
        cn_roughness_rain=rain_r,          # engine name (was rain_to_remove_rough: never used)
        musle_k=k, musle_p=p_fac, slope_pct=slope, slope_length=length, rill_ratio=rill,
        bulk_density=sum(bd) / len(bd), cracking=False, crack_infil=0.0)
    profile.total_depth = depths[-1]
    profile.pawc_total = sum(l.pawc for l in layers)
    profile.pawc = profile.pawc_total
    profile.sw_prop_no_stress = 0.2
    profile.source_file = str(path)
    profile.sediment_delivery_ratio = sdr      # carried for reference; not used by the engine

    # ── Optional settings, same meaning as the .soil XML tags ────────────────
    amc = _text(_get_row(df, 'amc'))
    if amc:
        profile.amc_mode = amc.lower()
    for kws, attr, cast in [(('sediment', 'cover', 'coef'), 'sed_cover_coef', float),
                            (('sediment', 'winter'), 'sed_winter_frac', float),
                            (('cover effect', 'winter'), 'cover_winter_frac', float),
                            (('storm', 'peak'), 'storm_peak_doy', int)]:
        row = _get_row(df, *kws)
        if row is not None:
            vs = _row_values(row, 1)
            if vs:
                setattr(profile, attr, cast(vs[0]))
    return profile
