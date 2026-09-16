"""
PERFECT model — daily soil water balance engine

Implements the key hydrological processes in order:
  1. Runoff          (SCS curve number method)
  2. Infiltration    (rainfall - runoff, distributed to layers)
  3. Drainage        (cascade: excess above DUL drains to layer below)
  4. Soil evaporation (two-stage: Ritchie model)
  5. Transpiration   (limited by potential ET and root water uptake)
  6. Deep drainage   (water draining below bottom layer)

References:
  Littleboy et al. (1992) PERFECT v2.0 manual, DAQ
  Ritchie (1972) two-stage soil evaporation
  SCS (1972) curve number runoff
"""

import numpy as np


# ---------------------------------------------------------------------------
# 1. Runoff — SCS curve number
# ---------------------------------------------------------------------------

def calc_cn(cn2, cover_frac, cn_cover_reduction, tillage_reduction=0.0):
    """
    Adjust CN2 for surface cover and tillage, then return CN1/CN2/CN3
    and the effective CN for current conditions.

    cover_frac              : 0–1 fractional ground cover
    cn_cover_reduction      : max CN reduction at 100% cover
    """
    cn2_adj = cn2 - cover_frac * cn_cover_reduction - tillage_reduction
    cn2_adj = np.clip(cn2_adj, 1.0, 99.0)
    # AMC-I and AMC-III from CN2
    cn1 = cn2_adj / (2.383 - 0.0123 * cn2_adj)
    cn3 = cn2_adj * np.exp(0.00673 * (100.0 - cn2_adj))
    return cn1, cn2_adj, cn3


def calc_runoff(rain_mm, cn2, cover_frac, cn_cover_reduction,
                tillage_reduction=0.0, sw_ratio=None):
    """
    SCS-CN runoff — HowLeaky V5 implementation.

    Uses the CREAMS/HowLeaky approach (eq 3-9):
        S = smx * (1 - sumh20)
    where smx is maximum S under dry conditions (from CN1),
    and sumh20 is the layer-weighted soil wetness (passed as sw_ratio).

    sw_ratio : float or None
        sumh20 — layer-weighted soil water ratio from airdry to SAT.
        Range 0 (airdry) to 1 (saturated).
        If None, uses fixed CN2 (bare soil / no AMC).

    Returns runoff (mm).
    """
    if rain_mm <= 0:
        return 0.0

    cn1, cn2_eff, cn3 = calc_cn(cn2, cover_frac, cn_cover_reduction,
                                  tillage_reduction)

    if sw_ratio is not None:
        # HowLeaky eq 3-12: smx = 254*(100/cn1 - 1)
        smx = 254.0 * (100.0 / max(cn1, 1.0) - 1.0)
        # HowLeaky eq 3-9: S = smx * (1 - sumh20)
        sumh20 = max(0.0, min(1.0, sw_ratio))
        s = smx * (1.0 - sumh20)
    else:
        s = 254.0 * (100.0 / cn2_eff - 1.0)

    ia = 0.2 * s
    if rain_mm <= ia:
        return 0.0
    runoff = (rain_mm - ia) ** 2 / (rain_mm - ia + s)
    return max(0.0, runoff)


# ---------------------------------------------------------------------------
# 2. Infiltration and redistribution
# ---------------------------------------------------------------------------

def infiltrate_and_drain(sw, layers, infil_mm):
    """
    Add infiltration to the soil profile and cascade excess water downward.

    Process per layer (top to bottom):
      - Add incoming water
      - Water above SAT cannot be held — clips to SAT, excess returned upward
        as surface overflow (added to runoff by caller)
      - Water above DUL (but <= SAT) drains downward, limited by Ksat
      - Residual input carries to the next layer

    Returns
    -------
    sw         : updated soil water array (mm per layer)
    deep_drain : water leaving the bottom layer (mm)
    overflow   : water that could not enter the profile (mm) — add to runoff
    """
    sw = sw.copy()
    input_mm  = infil_mm
    overflow  = 0.0      # water rejected by saturated profile → extra runoff

    for i, layer in enumerate(layers):
        sw[i] += input_mm

        # ── Cap at saturation — excess cannot infiltrate further ──────────
        if sw[i] > layer.sat_mm:
            overflow += sw[i] - layer.sat_mm
            sw[i]     = layer.sat_mm

        # ── Drain excess above DUL downward, limited by Ksat ─────────────
        if sw[i] > layer.dul_mm:
            ksat_day = layer.ksat * 24.0
            drain    = min(sw[i] - layer.dul_mm, ksat_day)
            sw[i]   -= drain
            input_mm = drain
        else:
            input_mm = 0.0

    # Water draining from the bottom layer leaves the profile
    deep_drain = input_mm

    # Enforce lower bound (airdry) — should never trigger but safety net
    for i, layer in enumerate(layers):
        sw[i] = max(sw[i], layer.airdry_mm)

    return sw, deep_drain, overflow


# ---------------------------------------------------------------------------
# 3. Soil evaporation — Ritchie two-stage model
# ---------------------------------------------------------------------------

def calc_soil_evap(sw, layers, eos, u, cona, sumes1, sumes2, t_since_wet):
    """
    Ritchie two-stage soil evaporation — exact HowLeaky V5 implementation.
    (HowLeaky Manual 2018, equations 3-32 to 3-39)

    Uses sse1 (Stage I accumulator) and sse2 (Stage II accumulator).
    dsr (days since rain) is derived from sse2: dsr = (sse2/Cona)^2

    sumes1 = sse1, sumes2 = sse2, t_since_wet unused (dsr computed from sse2).

    eos   : potential soil evaporation (mm/day)
    u     : Stage I upper limit = Stage1SoilEvapLimit (mm)
    cona  : Stage II coefficient (mm/day^0.5)

    Returns (es, sumes1, sumes2, t_since_wet).
    """
    if eos <= 0:
        return 0.0, sumes1, sumes2, t_since_wet

    sse1 = sumes1
    sse2 = sumes2

    # Available water for evaporation from layers 1 and 2
    paw0      = max(0.0, sw[0] - layers[0].airdry_mm)           # layer 1 to airdry
    paw0_ad   = layers[0].airdry_mm                              # airdry layer 1
    if len(layers) > 1:
        l2_limit = layers[1].airdry_mm + 0.5*(layers[1].ll_mm - layers[1].airdry_mm)
        paw1     = max(0.0, sw[1] - l2_limit)                   # layer 2 to midpoint
        paw1_ad  = l2_limit
    else:
        paw1 = 0.0; paw1_ad = 0.0

    # ── Stage I (eq 3-32, 3-33) ──────────────────────────────────────────────
    se1 = min(eos, u - sse1)                                     # eq 3-32
    se1 = max(0.0, min(se1, paw0 + paw0_ad))                    # eq 3-33 (layer 1)
    sse1 += se1                                                  # eq 3-34

    se2 = 0.0
    # ── Stage II — only if Stage I demand not fully met (eq 3-35/3-36) ──────
    if eos > se1:
        # dsr = days since rain, computed from sse2 (eq 3-31)
        dsr = (sse2 / cona) ** 2 if sse2 > 0 else 0.0
        if sse2 > 0:
            # Increment dsr by 1 day, compute new cumulative potential
            # se2 = Cona*sqrt(dsr+1) - sse2  (daily increment of sqrt curve)
            # eq 3-35
            se2 = min(eos - se1, cona * ((dsr + 1.0) ** 0.5) - sse2)
            se2 = max(0.0, se2)
        else:
            # First entry into Stage II: use transition constant 0.6 (eq 3-36)
            se2 = 0.6 * (eos - se1)
            se2 = max(0.0, se2)

        # Distribute se2 across layers 1 and 2 (eq 3-37, 3-38, 3-39)
        se21 = max(0.0, min(se2, paw0 + paw0_ad))
        se22 = max(0.0, min(se2 - se21, paw1 + paw1_ad))
        se2  = se21 + se22                                       # eq 3-39
        sse2 += se2

    es = se1 + se2
    return es, sse1, sse2, t_since_wet


def reset_evap_accumulators(rain_mm, sumes1, sumes2, t_since_wet, u, infil_mm=None):
    """
    Reset evaporation accumulators after infiltration.
    HowLeaky V5 equations 3-29 and 3-30:

        sse2 = max(0, sse2 - max(0, infiltration - sse1))
        sse1 = max(0, sse1 - infiltration)

    Stage I is reduced by infiltration first. Any excess infiltration
    beyond sse1 then reduces sse2. This means small rains only reset
    Stage I (partial), while large rains also reduce Stage II.
    """
    infil = infil_mm if infil_mm is not None else rain_mm
    if infil <= 0:
        return sumes1, sumes2, t_since_wet

    sse1 = sumes1
    sse2 = sumes2

    # eq 3-29: sse2 reduced by infiltration exceeding sse1
    sse2 = max(0.0, sse2 - max(0.0, infil - sse1))
    # eq 3-30: sse1 reduced by infiltration
    sse1 = max(0.0, sse1 - infil)

    return sse1, sse2, t_since_wet


# ---------------------------------------------------------------------------
# 4. Transpiration / root water extraction
# ---------------------------------------------------------------------------

def calc_transpiration(sw, layers, ep, root_depth_mm):
    """
    Extract transpiration water from rooted layers proportional to
    plant available water in each layer.

    ep           : potential transpiration (mm/day)
    root_depth_mm: current rooting depth

    Returns (actual_transp, updated sw).
    """
    sw = sw.copy()
    if ep <= 0 or root_depth_mm <= 0:
        return 0.0, sw

    # Determine which layers are within rooting depth
    cum_depth = 0.0
    avail = []
    root_fracs = []  # fraction of layer within root zone
    for idx, layer in enumerate(layers):
        cum_depth_prev = cum_depth
        cum_depth += layer.thickness
        if cum_depth_prev >= root_depth_mm:
            avail.append(0.0)
            root_fracs.append(0.0)
        else:
            root_frac = min(1.0, (root_depth_mm - cum_depth_prev) / layer.thickness)
            root_fracs.append(root_frac)
            # Ensure non-negative — sw[idx] may be at ll_mm already
            avail.append(max(0.0, sw[idx] - layer.ll_mm) * root_frac)

    total_avail = sum(avail)
    if total_avail <= 0:
        return 0.0, sw

    # Actual transpiration limited by availability
    transp = min(ep, total_avail)

    # Extract proportionally from each layer, track what was actually removed
    actual_transp = 0.0
    for i, (av, rf) in enumerate(zip(avail, root_fracs)):
        if av > 0 and total_avail > 0:
            extract = transp * (av / total_avail)
            sw_before = sw[i]
            sw[i] = max(sw[i] - extract, layers[i].ll_mm)
            actual_transp += sw_before - sw[i]

    return max(0.0, actual_transp), sw


# ---------------------------------------------------------------------------
# 5. Potential ET partitioning
# ---------------------------------------------------------------------------

def partition_et(epan, green_cover, crop_factor=1.0):
    """
    Split pan evaporation into potential soil evaporation (eos)
    and potential transpiration (ep).

    green_cover : fraction of ground covered by GREEN (living) canopy
                  — drives radiation interception and transpiration demand
    crop_factor : scales total PET (default 1.0)

    Soil evap is reduced under the canopy (Beer's law approximation).
    Transpiration demand is proportional to green cover fraction.
    """
    pet = epan * crop_factor
    # HowLeaky eq 3-25: eos = pan_evap * (1 - total_cover * 0.87)
    # green_cover here is passed as total_cover when green > 0
    # For bare soil (green_cover=0) total_cover=0 so eos=pet
    eos = pet * (1.0 - green_cover * 0.87)
    ep  = pet * green_cover
    return eos, ep



# ---------------------------------------------------------------------------
# 7. Erosion — PERFECT sediment concentration model
# ---------------------------------------------------------------------------

def calc_ls_factor(slope_pct, slope_length_m, rill_ratio=1.0):
    """
    Compute the USLE LS (slope length-gradient) factor.

    Uses the McCool et al. (1987) equations as implemented in PERFECT:
      S  = sin(theta) based
      L  = (slope_length / 22.13) ^ m
      m  = 0.6 for slopes > 5%, 0.5 for 3-5%, 0.4 for 1-3%, 0.3 for < 1%

    rill_ratio adjusts for rill vs interrill contribution.
    """
    import math
    theta = math.atan(slope_pct / 100.0)
    sin_t = math.sin(theta)

    # S factor (McCool steep-slope equation)
    if slope_pct >= 9.0:
        s_factor = 16.8 * sin_t - 0.50
    else:
        s_factor = 10.8 * sin_t + 0.03

    # L factor exponent m based on slope
    if slope_pct > 5.0:
        m = 0.6
    elif slope_pct >= 3.0:
        m = 0.5
    elif slope_pct >= 1.0:
        m = 0.4
    else:
        m = 0.3

    l_factor = (slope_length_m / 22.13) ** m
    return l_factor * s_factor * rill_ratio


def calc_erosion(runoff_mm, total_cover_frac, ls_factor, kusle, pusle):
    """
    PERFECT daily sediment yield (t/ha).

    Directly translates the Fortran erosion subroutine:

        sed = 0
        if runf <= 1: return
        cover = min(100, (covm + ccov) * 100)          # total cover %
        if cover < 50: conc = 16.52 - 0.46*cover + 0.0031*cover^2
        if cover >= 50: conc = -0.0254*cover + 2.54
        conc = max(0, conc)
        sed = conc * ls * kusle * pusle * runf / 10

    Parameters
    ----------
    runoff_mm        : float  daily runoff (mm)
    total_cover_frac : float  total ground cover fraction 0–1 (green + residue)
    ls_factor        : float  pre-computed LS factor for the site
    kusle            : float  soil erodibility K factor
    pusle            : float  support practice P factor

    Returns
    -------
    sed : float  sediment yield (t/ha/day), 0 if runoff <= 1 mm
    """
    if runoff_mm <= 1.0:
        return 0.0

    cover = min(100.0, total_cover_frac * 100.0)

    if cover < 50.0:
        conc = 16.52 - 0.46 * cover + 0.0031 * cover * cover
    else:
        conc = -0.0254 * cover + 2.54

    conc = max(0.0, conc)
    sed = conc * ls_factor * kusle * pusle * runoff_mm / 10.0
    return sed

# ---------------------------------------------------------------------------
# 6. Main daily step
# ---------------------------------------------------------------------------

def daily_water_balance(
        sw, layers, soil,
        rain, epan,
        green_cover, total_cover, root_depth_mm, crop_factor,
        sumes1, sumes2, t_since_wet,
        tillage_cn_reduction=0.0
    ):
    """
    Run one day of the PERFECT soil water balance.

    Parameters
    ----------
    sw              : np.array  soil water per layer (mm)
    layers          : list of SoilLayer
    soil            : SoilProfile (holds CN, Cona, U etc.)
    rain            : float  daily rainfall (mm)
    epan            : float  pan evaporation (mm)
    green_cover     : float  green (living) canopy cover fraction (0–1)
                             — used for ET partitioning
    total_cover     : float  total ground cover fraction (green + residue, 0–1)
                             — used for runoff CN reduction
    root_depth_mm   : float  current rooting depth (mm)
    crop_factor     : float  ET crop factor (1.0 = reference pan)
    sumes1/2        : floats  accumulated soil evap stage I/II (mm)
    t_since_wet     : float  days since last wetting (for stage II)
    tillage_cn_reduction : float  CN reduction from recent tillage

    Returns
    -------
    dict of daily fluxes + updated state variables
    """

    # -- 1. Runoff — uses TOTAL cover (green + residue) -----------------------
    # HowLeaky AMC: sumh20 = layer-weighted soil water from airdry to SAT (eq 3-17)
    # sumh20 = Σ WFi × (PAWi + AirDryLimit_i) / (SatLimit_i + AirDryLimit_i)
    # WFi = 1.016 * (exp(-4.16*depth_i/depth_max) - exp(-4.16*depth_{i+1}/depth_max))
    # Only applied when crop is actively growing; bare soil uses fixed CN2.
    if green_cover > 0.01:
        depth_max = layers[-1].depth_mm
        sumh20 = 0.0
        for i, l in enumerate(layers):
            depth_i   = layers[i-1].depth_mm if i > 0 else 0.0
            depth_i1  = l.depth_mm
            wfi = 1.016 * (np.exp(-4.16 * depth_i  / depth_max) -
                           np.exp(-4.16 * depth_i1 / depth_max))
            paw_i    = max(0.0, sw[i] - l.airdry_mm)
            sat_lim  = l.sat_mm + l.airdry_mm
            if sat_lim > 0:
                sumh20 += wfi * (paw_i + l.airdry_mm) / sat_lim
        sw_ratio = max(0.0, min(1.0, sumh20))
    else:
        sw_ratio = None   # bare/fallow — use fixed CN2 (no AMC)
    runoff = calc_runoff(rain, soil.cn2_bare, total_cover,
                         soil.cn_cover_reduction, tillage_cn_reduction,
                         sw_ratio=sw_ratio)
    infil = max(0.0, rain - runoff)

    # -- 2. Reset evap accumulators if significant rain -----------------------
    # Reset evap accumulators based on actual infiltration (rain - runoff)
    _infil_for_reset = max(0.0, rain - runoff)
    sumes1, sumes2, t_since_wet = reset_evap_accumulators(
        rain, sumes1, sumes2, t_since_wet, soil.u, infil_mm=_infil_for_reset)

    # -- 3. Infiltrate and drain ----------------------------------------------
    # overflow = water the profile cannot absorb (SAT exceeded) → add to runoff
    sw, deep_drain, overflow = infiltrate_and_drain(sw, layers, infil)
    runoff += overflow          # profile-full overflow is surface runoff
    infil  -= overflow          # adjust infil to what actually entered

    # -- 4. Partition ET — uses GREEN cover for transpiration demand ----------
    # HowLeaky uses total_cover (not green_cover) for eos reduction (eq 3-25)
    eos, ep = partition_et(epan, total_cover, crop_factor)

    # -- 5. Soil evaporation --------------------------------------------------
    es, sumes1, sumes2, t_since_wet = calc_soil_evap(
        sw, layers, eos, soil.u, soil.cona, sumes1, sumes2, t_since_wet)
    # Extract es from layers 1 and 2:
    # Layer 1: down to airdry; Layer 2: down to midpoint between airdry and LL
    avail_l1 = max(0.0, sw[0] - layers[0].airdry_mm)
    if len(layers) > 1:
        l2_limit = layers[1].airdry_mm + 0.5*(layers[1].ll_mm - layers[1].airdry_mm)
        avail_l2 = max(0.0, sw[1] - l2_limit)
    else:
        avail_l2 = 0.0

    # Take from layer 1 first, then layer 2
    take_l1   = min(es, avail_l1)
    take_l2   = min(es - take_l1, avail_l2)
    es_actual = take_l1 + take_l2

    sw[0] = max(sw[0] - take_l1, layers[0].airdry_mm)
    if len(layers) > 1:
        l2_limit = layers[1].airdry_mm + 0.5*(layers[1].ll_mm - layers[1].airdry_mm)
        sw[1] = max(sw[1] - take_l2, l2_limit)
    es = es_actual

    # -- 6. Transpiration -----------------------------------------------------
    transp, sw = calc_transpiration(sw, layers, ep, root_depth_mm)

    # -- Summary water balance check ------------------------------------------
    total_sw = sw.sum()

    # -- 7. Erosion -----------------------------------------------------------
    ls  = calc_ls_factor(soil.slope_pct, soil.slope_length, soil.rill_ratio)
    sed = calc_erosion(runoff, total_cover, ls, soil.musle_k, soil.musle_p)

    return {
        'sw'          : sw,
        'sw_total'    : total_sw,
        'runoff'      : runoff,
        'infil'       : infil,
        'drainage'    : deep_drain,
        'soil_evap'   : es,
        'transp'      : transp,
        'et'          : es + transp,
        'sediment'    : sed,
        'sumes1'      : sumes1,
        'sumes2'      : sumes2,
        't_since_wet' : t_since_wet,
    }


# ---------------------------------------------------------------------------
# Convenience: run full simulation over a climate DataFrame
# ---------------------------------------------------------------------------

def run_simulation(met_df, profile, cover_frac=0.0, root_depth_mm=300.0,
                   crop_factor=1.0, sw_init_frac=0.5):
    """
    Run a multi-year daily water balance simulation.

    met_df      : DataFrame from read_met() or fetch_silo()
    profile     : SoilProfile from read_prm()
    cover_frac  : constant fractional cover — used as BOTH green and total
                  (appropriate for bare fallow or simple uniform cover)
    root_depth  : constant root depth (mm)
    crop_factor : ET scaling factor
    sw_init_frac: initial SW as fraction of PAWC above LL

    Returns a DataFrame of daily outputs.
    """
    from soil import init_sw
    import pandas as pd

    layers = profile.layers
    sw = init_sw(profile, sw_init_frac)

    sumes1, sumes2, t_since_wet = 0.0, 0.0, 0.0

    records = []
    for date, row in met_df.iterrows():
        rain = row.get('rain', 0.0)
        epan = row.get('epan', 0.0)
        if np.isnan(rain): rain = 0.0
        if np.isnan(epan): epan = 0.0

        out = daily_water_balance(
            sw=sw, layers=layers, soil=profile,
            rain=rain, epan=epan,
            green_cover=cover_frac,   # same for simple bare/uniform scenarios
            total_cover=cover_frac,
            root_depth_mm=root_depth_mm,
            crop_factor=crop_factor,
            sumes1=sumes1, sumes2=sumes2,
            t_since_wet=t_since_wet,
        )

        sw           = out['sw']
        sumes1       = out['sumes1']
        sumes2       = out['sumes2']
        t_since_wet  = out['t_since_wet']

        rec = {
            'date'      : date,
            'rain'      : rain,
            'epan'      : epan,
            'runoff'    : out['runoff'],
            'infil'     : out['infil'],
            'drainage'  : out['drainage'],
            'soil_evap' : out['soil_evap'],
            'transp'    : out['transp'],
            'et'        : out['et'],
            'sw_total'  : out['sw_total'],
        }
        # Per-layer SW
        for i, s in enumerate(sw):
            rec[f'sw_layer{i+1}'] = s

        records.append(rec)

    df = pd.DataFrame(records).set_index('date')
    return df
