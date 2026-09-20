"""
core/cover_excel.py
===================
Reader for Excel-format vegetation/cover schedule files.

Expected layout (sheet "Main"):
  Row 0:  Title
  Row 1:  Count, N
  Row 2:  Headers (Day/Month | Day No | Green Cover % | Residue Cover % | Root Depth mm | Plot)
  Row 3+: Data rows (N rows)
  Then parameter rows (any order, matched by label):
    Plant day                  | value | units
    Days to harvest            | value | units
    Transpiration efficiency   | value | kg/ha/mm
    Harvest index              | value | fraction
    PAW no crop stress         | value | fraction  (SWPropForNoStress)
    Green cover multiplier     | value |
    Residue cover multiplier   | value |
    Root depth multiplier      | value |
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CoverSchedule:
    name:          str
    source_file:   str
    n_points:      int
    doy:           np.ndarray   # day of year
    green_cover:   np.ndarray   # fraction 0-1
    residue_cover: np.ndarray   # fraction 0-1
    total_cover:   np.ndarray   # fractional cover (green + residue*(1-green))
    root_depth:    np.ndarray   # mm
    # crop parameters
    plant_day:     int   = 150
    days_to_harvest: int = 150
    tue:           float = 30.0   # transpiration use efficiency kg/ha/mm
    hi:            float = 0.4    # harvest index
    sw_prop_no_stress: float = 0.2  # PAW fraction below which stress begins
    # multipliers
    green_mult:    float = 1.0
    residue_mult:  float = 1.0
    root_mult:     float = 1.0


def _find_param(df: pd.DataFrame, *labels) -> float | None:
    """Search all rows for a parameter by label keyword(s), return first numeric value found."""
    labels_lower = [l.lower() for l in labels]
    for _, row in df.iterrows():
        cell0 = str(row.iloc[0]).lower()
        if all(k in cell0 for k in labels_lower):
            for v in row.iloc[1:]:
                try:
                    f = float(v)
                    if f == f:   # NaN check
                        return f
                except (ValueError, TypeError):
                    pass
    return None


def read_cover_excel(path: str | Path) -> CoverSchedule:
    """Read an Excel vegetation/cover schedule file."""
    path = Path(path)
    df = pd.read_excel(path, sheet_name=0, header=None)

    # ── Count ─────────────────────────────────────────────────────────────────
    n = 14
    for _, row in df.iterrows():
        if str(row.iloc[0]).strip().lower() == 'count':
            try:
                n = int(float(row.iloc[1]))
            except (ValueError, TypeError):
                pass
            break

    # ── Find header row ───────────────────────────────────────────────────────
    header_row = None
    for i, row in df.iterrows():
        if 'day no' in str(row.iloc[1]).lower() or 'day no' in str(row.iloc[0]).lower():
            header_row = i
            break
    if header_row is None:
        header_row = 2

    # ── Data rows ─────────────────────────────────────────────────────────────
    data = df.iloc[header_row + 1: header_row + 1 + n].reset_index(drop=True)

    doy     = data.iloc[:, 1].astype(float).values
    green   = data.iloc[:, 2].astype(float).values / 100.0
    residue = data.iloc[:, 3].astype(float).values / 100.0
    roots   = data.iloc[:, 4].astype(float).values

    # ── Parameters from labelled rows ─────────────────────────────────────────
    params_df = df.iloc[header_row + 1 + n:]

    plant_day        = _find_param(params_df, 'plant', 'day')           or 150
    days_to_harvest  = _find_param(params_df, 'days', 'harvest')        or 150
    tue              = _find_param(params_df, 'transpiration', 'effic') or 30.0
    hi               = _find_param(params_df, 'harvest', 'index')       or 0.4
    sw_prop          = _find_param(params_df, 'paw', 'stress')          or 0.2
    green_mult       = _find_param(params_df, 'green', 'mult')          or 1.0
    residue_mult     = _find_param(params_df, 'residue', 'mult')        or 1.0
    root_mult        = _find_param(params_df, 'root', 'mult')           or 1.0

    # Apply multipliers
    green   = np.clip(green   * green_mult,   0.0, 1.0)
    residue = np.clip(residue * residue_mult, 0.0, 1.0)
    roots   = roots * root_mult

    total = green + (1.0 - green) * residue

    return CoverSchedule(
        name            = path.stem,
        source_file     = str(path),
        n_points        = n,
        doy             = doy,
        green_cover     = green,
        residue_cover   = residue,
        total_cover     = total,
        root_depth      = roots,
        plant_day       = int(plant_day),
        days_to_harvest = int(days_to_harvest),
        tue             = float(tue),
        hi              = float(hi),
        sw_prop_no_stress = float(sw_prop),
        green_mult      = float(green_mult),
        residue_mult    = float(residue_mult),
        root_mult       = float(root_mult),
    )


def get_cover_state(schedule: CoverSchedule, doy: int):
    """
    Interpolate green cover (fraction), total cover (fraction),
    and root depth (mm) for a given day of year.
    Root depth is held at seasonal maximum while green cover is present.
    Returns (green_cover, total_cover, root_depth_mm).
    """
    green = float(np.interp(doy, schedule.doy, schedule.green_cover))
    total = float(np.interp(doy, schedule.doy, schedule.total_cover))
    roots = float(np.interp(doy, schedule.doy, schedule.root_depth))

    # Hold root depth at seasonal maximum while green cover present
    if green > 0.01:
        past_roots = [r for d, r in zip(schedule.doy, schedule.root_depth) if d <= doy]
        if past_roots:
            roots = max(roots, max(past_roots))
    roots = max(0.0, roots)
    return green, total, roots
