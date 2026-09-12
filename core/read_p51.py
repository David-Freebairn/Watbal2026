"""
SILO P51 file reader
====================
Handles both P51 variants:

  Comma-separated (newer SILO downloads):
    date,jday,tmax,tmin,rain,evap,rad,vp[,]
    19770101,1,33.8,17.7,0,10,29,19,

  Whitespace-separated (classic SILO / older files):
    date    jday  tmax  tmin  rain  evap   rad   vp
   19000101    1  30.0  22.0   5.6   6.0  24.0  26.0

Line 1 is always:  lat lon station_no name   (whitespace-sep)
Comment lines start with // or #
"""

import re
import pandas as pd
import numpy as np
from pathlib import Path


def read_p51(filepath):
    """
    Parse a SILO .P51 climate file (comma or whitespace separated).

    Returns
    -------
    lat : float
    df  : pd.DataFrame  daily climate indexed by date
    """
    filepath = Path(filepath)

    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()

    # ── Line 1: lat lon [extra tokens] station_no name ──────────────────────
    # Formats seen:
    #   "-27.33 151.62  4135 OAKEY AERO"
    #   "-22.98 150.26 syn pan pre 70  33003BALMORAL STA"
    #   "-28.80 114.70 syn pan pre 70   8051GERALDTON AIRPOR"  (space before number)
    # Strategy: scan the raw line (not split tokens) for a run of digits
    # that looks like a station number, take everything after as the name.
    line1 = lines[0].strip()
    parts = line1.split()
    lat = float(parts[0])
    lon = float(parts[1])
    name = filepath.stem   # fallback

    # Search the raw line for a station-number+name pattern
    # Station numbers are 3-6 digits optionally preceded by spaces
    m = re.search(r'\s(\d{3,6})([A-Z].*)', line1)
    if m:
        stn_num = m.group(1)
        name    = m.group(2).strip()
    else:
        # Try digits-only token followed by name tokens
        for k in range(2, len(parts)):
            if re.match(r'^\d{3,6}$', parts[k]):
                rest = ' '.join(parts[k+1:]).strip()
                if rest:
                    name = rest
                break

    # ── Find header line ──────────────────────────────────────────────────────
    header_idx = None
    for i, line in enumerate(lines[1:], 1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith('//') or stripped.startswith('#'):
            continue
        if stripped.lower().startswith('date'):
            header_idx = i
            break

    if header_idx is None:
        raise ValueError(f"Could not find header line in {filepath.name}")

    # ── Detect separator from first data line ─────────────────────────────────
    # Look ahead to first non-empty data line
    sep = None
    for line in lines[header_idx + 1:]:
        stripped = line.strip()
        if stripped and not stripped.startswith('//'):
            sep = ',' if ',' in stripped else None   # None = whitespace
            break

    # ── Column names from header ──────────────────────────────────────────────
    header_cols = lines[header_idx].strip().lower().split()

    # Column name → our internal name
    COL_MAP = {
        'tmax': 'tmax', 'tmin': 'tmin',
        'rain': 'rain', 'evap': 'epan',
        'rad' : 'radiation', 'vp': 'vp',
    }

    # ── Parse data lines ──────────────────────────────────────────────────────
    records = []
    for line in lines[header_idx + 1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith('//') or stripped.startswith('#'):
            continue

        # Split
        if sep == ',':
            row = stripped.rstrip(',').split(',')
        else:
            row = stripped.split()   # whitespace — handles any run of spaces

        if len(row) < 2:
            continue

        # Date — always first column, YYYYMMDD integer
        try:
            date_int = int(row[0])
            if date_int < 18000101 or date_int > 21001231:
                continue
        except ValueError:
            continue

        year  = date_int // 10000
        month = (date_int % 10000) // 100
        day   = date_int % 100

        try:
            ts = pd.Timestamp(year=year, month=month, day=day)
        except Exception:
            continue

        rec = {
            'date' : ts,
            'year' : year,
            'month': month,
            'day'  : day,
            'doy'  : int(float(row[1])) if len(row) > 1 else np.nan,
        }

        # Remaining columns by position matching header
        for j, col in enumerate(header_cols[2:], 2):
            key = COL_MAP.get(col, col)
            if j < len(row):
                try:
                    rec[key] = float(row[j])
                except (ValueError, IndexError):
                    rec[key] = np.nan
            else:
                rec[key] = np.nan

        records.append(rec)

    if not records:
        raise ValueError(f"No valid data rows found in {filepath.name}")

    df = pd.DataFrame(records).set_index('date')
    df['tmean'] = (df['tmax'] + df['tmin']) / 2.0

    # Ensure all expected columns exist
    for col in ['rain', 'epan', 'tmax', 'tmin', 'radiation', 'vp']:
        if col not in df.columns:
            df[col] = np.nan

    # Fallback: estimate epan from radiation if missing
    if df['epan'].isna().all() or df['epan'].sum() < 1.0:
        rs    = df['radiation'].fillna(df['radiation'].median())
        tmean = df['tmean'].fillna(20.0)
        df['epan'] = (rs * 0.50 + tmean * 0.06).clip(lower=0.5)

    df['epan'] = df['epan'].fillna(0.0)
    df['rain'] = df['rain'].fillna(0.0).clip(lower=0.0)

    # Remove duplicate dates
    df = df[~df.index.duplicated(keep='last')].sort_index()

    print(f"  Loaded {filepath.name}: {name} ({lat:.3f}, {lon:.3f})")
    print(f"  Period: {df.index[0].date()} to {df.index[-1].date()}"
          f"  ({len(df)} days, {df.index.year.nunique()} years)")

    return lat, df


if __name__ == '__main__':
    import sys
    fpath = sys.argv[1] if len(sys.argv) > 1 else '/mnt/user-data/uploads/Greenwood.p51'
    lat, df = read_p51(fpath)
    print(f"\nColumns: {list(df.columns)}")
    print(df[['tmax','tmin','rain','epan','radiation']].describe().round(2))
    print(f"\nFirst 5 rows:")
    print(df[['rain','epan','tmax','tmin']].head())
