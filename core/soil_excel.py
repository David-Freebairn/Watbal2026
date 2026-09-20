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
  Row 9:  Bulk density,             v1, v2, ..., vN, g/cm
  Row 10: PAWC per layer (computed — ignored)
  Row 11: Stage 1 evap. (U),        value, mm
  Row 12: Stage 2 evap. (Cona),     value
  Row 13: Runoff Curve Number (CN), value
  Row 14: CN reduction cover,       value
  Row 15: Erodibility (K),          value
  Row 16: Field Slope (S),          value, %
  Row 17: Slope Length (L),         value, m
  Row 18: Practice factor (P),      value
  Row 19: CN Reduction - Tillage,   value
  Row 20: Rainfall to 0 roughness,  value
  Row 21: Sediment Delivery Ratio,  value
  Row 22: Rill/interrill ratio,     value
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass, field
from typing import List


@dataclass
class SoilLayer:
    depth_mm:   float   # cumulative depth to bottom of layer (mm)
    thickness:  float   # layer thickness (mm)
    airdry:     float   # airdry volumetric fraction
    ll:         float   # lower limit (WP) volumetric fraction
    dul:        float   # drained upper limit (FC) volumetric fraction
    sat:        float   # saturation volumetric fraction
    ksat:       float   # saturated hydraulic conductivity (mm/hr)

    @property
    def airdry_mm(self):  return self.airdry * self.thickness
    @property
    def ll_mm(self):      return self.ll  * self.thickness
    @property
    def dul_mm(self):     return self.dul * self.thickness
    @property
    def sat_mm(self):     return self.sat * self.thickness
    @property
    def pawc(self):       return (self.dul - self.ll) * self.thickness
    @property
    def ksat_mm_day(self):return self.ksat * 24.0


@dataclass
class SoilProfile:
    name:          str
    source_file:   str
    layers:        List[SoilLayer]
    u:             float = 4.0    # Stage 1 evap limit (mm)
    cona:          float = 4.0    # Stage 2 evap coeff
    cn2_bare:      float = 83.0   # CN2 bare soil
    cn_cover_reduction: float = 15.0
    musle_k:       float = 0.35
    slope_pct:     float = 7.0
    slope_length:  float = 60.0
    musle_p:       float = 1.0
    rill_ratio:    float = 1.0
    tillage_cn_reduction: float = 0.0
    rain_to_remove_rough: float = 0.0
    sediment_delivery_ratio: float = 1.0
    crack_infil:   float = 0.0
    sw_prop_no_stress: float = 0.2

    @property
    def pawc(self):
        return sum(l.pawc for l in self.layers)


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


def read_soil_excel(path: str | Path) -> SoilProfile:
    """Read an Excel soil description file."""
    path = Path(path)

    # Try to find the right sheet
    xl = pd.ExcelFile(path)
    sheet = xl.sheet_names[0]
    for s in xl.sheet_names:
        if 'soil' in s.lower() or 'descr' in s.lower():
            sheet = s; break

    df = pd.read_excel(path, sheet_name=sheet, header=None)

    # ── Soil name ─────────────────────────────────────────────────────────────
    name = path.stem
    for _, row in df.iterrows():
        cell = str(row.iloc[0]).lower()
        if 'soil name' in cell or 'name' == cell.strip():
            try:
                name = str(row.iloc[1]).strip()
            except Exception:
                pass
            break

    # ── Number of layers ──────────────────────────────────────────────────────
    n_row = _get_row(df, 'number', 'horizon')
    n = int(_scalar(n_row, 4))

    # ── Layer arrays ──────────────────────────────────────────────────────────
    _dep_row = _get_row(df, 'layer', 'depth', 'cumul')
    if _dep_row is None: _dep_row = _get_row(df, 'layer depth')
    depths  = _row_values(_dep_row, n)
    ad_pct  = _row_values(_get_row(df, 'air', 'dry'), n)
    ll_pct  = _row_values(_get_row(df, 'wilting'), n)
    _dul_row = _get_row(df, 'field', 'capacity')
    if _dul_row is None: _dul_row = _get_row(df, 'field cap')
    dul_pct = _row_values(_dul_row, n)
    sat_pct = _row_values(_get_row(df, 'sat'), n)
    _ksat_row = _get_row(df, 'drainage', 'layer')
    if _ksat_row is None: _ksat_row = _get_row(df, 'max', 'drain')
    ksat_d  = _row_values(_ksat_row, n)

    # Defaults if any missing
    if not depths:  depths  = [300*i for i in range(1, n+1)]
    if not ad_pct:  ad_pct  = [10.0]*n
    if not ll_pct:  ll_pct  = [30.0]*n
    if not dul_pct: dul_pct = [50.0]*n
    if not sat_pct: sat_pct = [60.0]*n
    if not ksat_d:  ksat_d  = [50.0]*n

    # Pad/trim to n layers
    for lst in [depths, ad_pct, ll_pct, dul_pct, sat_pct, ksat_d]:
        while len(lst) < n: lst.append(lst[-1])

    # Build layer objects
    layers = []
    cum = 0.0
    for i in range(n):
        thick = depths[i] - cum
        layers.append(SoilLayer(
            depth_mm  = depths[i],
            thickness = thick,
            airdry    = ad_pct[i]  / 100.0,
            ll        = ll_pct[i]  / 100.0,
            dul       = dul_pct[i] / 100.0,
            sat       = sat_pct[i] / 100.0,
            ksat      = ksat_d[i]  / 24.0,   # mm/day → mm/hr
        ))
        cum = depths[i]

    # ── Scalar parameters ─────────────────────────────────────────────────────
    u      = _scalar(_get_row(df, 'stage 1', 'evap'), 4.0)
    _cona_row = _get_row(df, 'stage 2', 'evap')
    if _cona_row is None: _cona_row = _get_row(df, 'cona')
    cona   = _scalar(_cona_row, 4.0)
    _cn_row = _get_row(df, 'runoff curve')
    if _cn_row is None: _cn_row = _get_row(df, 'curve number')
    cn2    = _scalar(_cn_row, 83.0)
    cn_red = _scalar(_get_row(df, 'cn reduction', 'cover'), 15.0)
    k      = _scalar(_get_row(df, 'erodib'), 0.35)
    _slope_row = _get_row(df, 'slope', '%')
    if _slope_row is None: _slope_row = _get_row(df, 'field slope')
    slope  = _scalar(_slope_row, 7.0)
    length = _scalar(_get_row(df, 'slope', 'length'), 60.0)
    _p_row = _get_row(df, 'practice', 'factor')
    if _p_row is None: _p_row = _get_row(df, 'p factor')
    p_fac  = _scalar(_p_row, 1.0)
    rill   = _scalar(_get_row(df, 'rill'), 1.0)
    till_cn= _scalar(_get_row(df, 'cn', 'till'), 0.0)
    rain_r = _scalar(_get_row(df, 'rainfall', 'rough'), 0.0)
    sdr    = _scalar(_get_row(df, 'sediment', 'deliv'), 1.0)

    return SoilProfile(
        name          = name,
        source_file   = str(path),
        layers        = layers,
        u             = u,
        cona          = cona,
        cn2_bare      = cn2,
        cn_cover_reduction = cn_red,
        musle_k       = k,
        slope_pct     = slope,
        slope_length  = length,
        musle_p       = p_fac,
        rill_ratio    = rill,
        tillage_cn_reduction = till_cn,
        rain_to_remove_rough = rain_r,
        sediment_delivery_ratio = sdr,
        crack_infil   = 0.0,
    )
