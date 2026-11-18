"""
PERFECT model — soil profile reader and initialisation
Parses .PRM files and sets up layer-by-layer water content arrays.
"""

import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from typing import List


@dataclass
class SoilLayer:
    """One soil horizon."""
    depth_mm   : float   # cumulative depth to bottom of layer
    thickness  : float   # layer thickness (mm)  — derived
    airdry     : float   # air-dry water content (%vol → fraction)
    ll         : float   # lower limit / wilting point (fraction)
    dul        : float   # drained upper limit / field capacity (fraction)
    sat        : float   # saturation (fraction)
    ksat       : float   # saturated hydraulic conductivity (mm/hr)
    # derived
    ll_mm      : float = 0.0   # ll in mm water
    dul_mm     : float = 0.0   # dul in mm water
    sat_mm     : float = 0.0   # sat in mm water
    airdry_mm  : float = 0.0
    pawc         : float = 0.0   # plant available water capacity (mm)
    bulk_density : float = 1.4   # bulk density (g/cm3)


@dataclass
class SoilProfile:
    name        : str
    layers      : List[SoilLayer]
    cona        : float   # stage-II evap coefficient (mm/day^0.5)
    u           : float   # stage-I evap limit (mm)
    cn2_bare    : float   # curve number, bare soil AMC-II
    cn_cover_reduction : float  # CN reduction at 100% cover
    cn_tillage_max : float
    cn_roughness_rain : float
    musle_k     : float
    musle_p     : float
    slope_pct   : float
    slope_length: float
    rill_ratio  : float
    bulk_density: float
    cracking    : bool
    crack_infil : float   # max infiltration via cracks (mm)

    # computed
    total_depth : float = 0.0
    pawc_total  : float = 0.0


# ── Curve number entered at average soil wetness ────────────────────────────
# Watbal2026 runs the uncapped (HowLeaky) AMC by default. It works from a dry-soil
# retention, Smax = S(CN1), with CN1 derived from an internal CN2, so internal CN2
# values are high (90-100) and unlike the CNs people know. The runoff curve number
# in a soil file is therefore the bare-soil CN at average wetness (reference
# wetness 0.25, the CN the model uses when the profile is at that wetness), close
# to the handbook meaning of CN2 and to PERFECT entries. It is converted once, on
# reading, to the internal CN2; the runoff calculation itself is unchanged.
# A soil that sets AMC mode "capped" keeps the original PERFECT method and reads CN as is.
AMC_MODE_DEFAULT = 'howleaky'
CN_REFERENCE_WETNESS_DEFAULT = 0.25


def cn_input_to_internal(cn_input, wetness=CN_REFERENCE_WETNESS_DEFAULT):
    """Bare-soil CN at reference wetness -> internal CN2 for the uncapped AMC."""
    s_ref = 25400.0 / cn_input - 254.0                 # retention at reference wetness
    smax = s_ref / (1.0 - wetness)                     # dry-soil retention
    cn1 = 25400.0 / (smax + 254.0)
    return 2.383 * cn1 / (1.0 + 0.0123 * cn1)          # inverse of CN1 = CN2/(2.383 - 0.0123 CN2)


def cn_internal_to_input(cn2, wetness=CN_REFERENCE_WETNESS_DEFAULT):
    """Internal CN2 -> bare-soil CN at reference wetness (inverse of cn_input_to_internal)."""
    cn1 = cn2 / (2.383 - 0.0123 * cn2)
    smax = 25400.0 / cn1 - 254.0
    return 25400.0 / ((1.0 - wetness) * smax + 254.0)


def apply_cn_reference(profile, wetness=None):
    """Settle the AMC mode and, for the uncapped AMC, convert the entered CN.

    Called once by every soil reader after the profile is built. The AMC mode is
    the soil's own setting, else AMC_MODE_DEFAULT (uncapped). For uncapped soils
    profile.cn2_bare is taken as the bare-soil CN at the reference wetness (the
    soil's own value, else 0.25) and replaced with the internal CN2; the entered
    value is kept as profile.cn_input for display. Capped soils read CN as is."""
    if getattr(profile, '_cn_converted', False):
        return profile
    mode = (getattr(profile, 'amc_mode', None) or AMC_MODE_DEFAULT).lower()
    profile.amc_mode = mode
    if mode != 'howleaky':
        return profile
    w = CN_REFERENCE_WETNESS_DEFAULT if wetness is None else float(wetness)
    profile.cn_input = profile.cn2_bare
    profile.cn_reference_wetness = w
    profile.cn2_bare = cn_input_to_internal(profile.cn2_bare, w)
    profile._cn_converted = True
    return profile


def read_prm(filepath):
    """Parse a PERFECT .PRM soil parameter file."""
    lines = [l.rstrip() for l in open(filepath, encoding='utf-8', errors='replace')]

    name = lines[0].strip()
    # Skip separator lines (---) to find data
    data_lines = [l for l in lines if not l.startswith('-') and l.strip()]

    # n horizons is first numeric line after name
    n_horizons = int(data_lines[1].split()[0])

    # Find horizon data lines — they start with a float (depth)
    horizon_lines = []
    for l in data_lines[2:]:
        parts = l.split()
        if len(parts) >= 6:
            try:
                float(parts[0]); float(parts[1]); float(parts[2])
                horizon_lines.append(parts)
                if len(horizon_lines) == n_horizons:
                    break
            except ValueError:
                continue

    layers = []
    prev_depth = 0.0
    for p in horizon_lines:
        depth = float(p[0])
        thick = depth - prev_depth
        ad  = float(p[1]) / 100.0
        ll  = float(p[2]) / 100.0
        dul = float(p[3]) / 100.0
        sat = float(p[4]) / 100.0
        ks  = float(p[5])
        layer = SoilLayer(
            depth_mm  = depth,
            thickness = thick,
            airdry    = ad,
            ll        = ll,
            dul       = dul,
            sat       = sat,
            ksat      = ks,
            ll_mm     = ll  * thick,
            dul_mm    = dul * thick,
            sat_mm    = sat * thick,
            airdry_mm = ad  * thick,
            pawc      = (dul - ll) * thick,
        )
        layers.append(layer)
        prev_depth = depth

    # Scalar parameters — read in order after horizon block
    # Find them by scanning for lines with a leading float
    scalar_lines = []
    in_horizons = False
    found_horizons = 0
    for l in data_lines[2:]:
        parts = l.split()
        if not parts:
            continue
        try:
            float(parts[0])
            if found_horizons < n_horizons:
                found_horizons += 1
            else:
                scalar_lines.append(float(parts[0]))
        except ValueError:
            continue

    (cona, u, cn2_bare, cn_cov, cn_till, cn_rain,
     musle_k, musle_p, slope_pct, slope_len, rill,
     bulk_density) = scalar_lines[:12]

    # Cracking
    crack_line = [l for l in data_lines if l.strip().lower().startswith(('y','n')) and
                  'crack' not in l.lower()[:2]]
    cracking_line = [l for l in lines if 'crack' in l.lower() and
                     l.strip()[0].lower() in ('y','n')]
    cracking = False
    crack_infil = float(scalar_lines[12]) if len(scalar_lines) > 12 else 10.0
    for l in lines:
        stripped = l.strip().lower()
        if stripped.startswith('y') and 'crack' in l.lower():
            cracking = True
        elif stripped.startswith('n') and 'crack' in l.lower():
            cracking = False

    profile = SoilProfile(
        name        = name,
        layers      = layers,
        cona        = cona,
        u           = u,
        cn2_bare    = cn2_bare,
        cn_cover_reduction = cn_cov,
        cn_tillage_max = cn_till,
        cn_roughness_rain = cn_rain,
        musle_k     = musle_k,
        musle_p     = musle_p,
        slope_pct   = slope_pct,
        slope_length= slope_len,
        rill_ratio  = rill,
        bulk_density= bulk_density,
        cracking    = False,      # cracking removed from engine (Oct 2026)
        crack_infil = 0.0,
    )
    profile.total_depth = sum(l.thickness for l in layers)
    profile.pawc_total  = sum(l.pawc for l in layers)
    apply_cn_reference(profile)
    return profile


def init_sw(profile, fraction=0.5):
    """
    Initialise soil water content for each layer.
    fraction=0.5 means halfway between LL and DUL.
    Returns array of SW (mm) per layer.
    """
    sw = np.array([
        l.ll_mm + fraction * l.pawc
        for l in profile.layers
    ])
    return sw


if __name__ == '__main__':
    p = read_prm('/mnt/user-data/uploads/AVERRBE.PRM')
    print(f"Soil: {p.name}")
    print(f"Layers: {len(p.layers)}   Total depth: {p.total_depth} mm   PAWC: {p.pawc_total:.1f} mm")
    print(f"CN2 bare: {p.cn2_bare}   Cona: {p.cona}   U: {p.u}")
    for i, l in enumerate(p.layers):
        print(f"  Layer {i+1}: {l.thickness:5.0f} mm thick  "
              f"LL={l.ll*100:.1f}%  DUL={l.dul*100:.1f}%  SAT={l.sat*100:.1f}%  "
              f"PAWC={l.pawc:.1f} mm")
    sw0 = init_sw(p)
    print(f"\nInitial SW (50% of PAWC): {sw0}  total={sw0.sum():.1f} mm")
