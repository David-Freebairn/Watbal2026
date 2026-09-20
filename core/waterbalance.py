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
        # AMC moisture adjustment:
        # CN2 (user input) is the normal/dry baseline — sets the maximum S.
        # Soil wetness (sumh20) can only INCREASE runoff above CN2, not reduce it.
        # This is consistent with the SCS framework: CN2=normal, CN3=wet.
        # The original HL approach (smx from CN1) over-reduced S on dry profiles
        # because CN1 is 15-25 units below CN2, making even dry soils appear
        # to generate less runoff than the user's entered CN2 would suggest.
        smx    = 254.0 * (100.0 / max(cn1,    1.0) - 1.0)  # S(CN1) — wet-side floor
        s2     = 254.0 * (100.0 / max(cn2_eff,1.0) - 1.0)  # S(CN2) — dry baseline
        sumh20 = max(0.0, min(1.0, sw_ratio))
        # HL formula scaled from CN1, capped at S(CN2)
        s_amc  = float(int(smx * (1.0 - sumh20)))
        s      = max(0.0, min(s2, s_amc))
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
    Uses HowLeaky swcon rate-limited drainage per layer:
        swcon[i] = 2 * ksat / (sat-dul + ksat)
        drain[i] = min(swcon * excess_above_dul, ksat)

    Overflow only occurs when the SURFACE layer (layer 0) exceeds SAT —
    not from deep layer backup. This matches HowLeaky behaviour where slow
    deep drainage keeps water in deep layers (not backing up to runoff).
    """
    sw = sw.copy()
    n = len(layers)

    # ── Add infiltration and drain top-down ──────────────────────────────
    # Each layer: receive water → drain excess to next layer → overflow
    # only if top layer exceeds SAT after drainage
    # Infiltration cascade with rate-limited drainage (HowLeaky swcon).
    # Process top-down: each layer drains to next at swcon rate.
    # If bottom layer can't accept, water backs up and overflows at surface.
    # CN runoff already removed water from rain — only residual saturation
    # overflow reaches surface.
    n_layers  = len(layers)
    overflow  = 0.0
    carry     = infil_mm

    for i, layer in enumerate(layers):
        sw[i] += carry
        carry   = 0.0

        # Drain to next layer (rate-limited by swcon)
        if sw[i] > layer.dul_mm:
            ksat_day = layer.ksat * 24.0
            sat_dul  = max(0.001, layer.sat_mm - layer.dul_mm)
            swcon    = 2.0*ksat_day/(sat_dul+ksat_day) if (sat_dul+ksat_day)>0 else 1.0
            excess   = sw[i] - layer.dul_mm
            drain    = min(swcon * excess, ksat_day)
            sw[i]   -= drain
            carry    = drain

        # Cap at saturation — truly excess becomes overflow back to surface
        if sw[i] > layer.sat_mm:
            overflow += sw[i] - layer.sat_mm
            sw[i]     = layer.sat_mm

    deep_drain = carry

    # ── Enforce lower bound ────────────────────────────────────────────────
    for i, layer in enumerate(layers):
        sw[i] = max(sw[i], layer.airdry_mm)

    return sw, deep_drain, overflow


# ---------------------------------------------------------------------------
# 3. Soil evaporation — Ritchie two-stage model
# ---------------------------------------------------------------------------

def calc_soil_evap(sw, layers, eos, u, cona, sumes1, sumes2, t_since_wet):
    """
    Ritchie two-stage soil evaporation — exact port of HowLeakyEngine.cpp
    CalculateSoilEvaporation() function.

    Variables map directly to C++ source:
        sse1 = sumes1  (Stage I cumulative evap)
        sse2 = sumes2  (Stage II cumulative evap)
        dsr  = t_since_wet (days since rain, derived from sse2)
        u    = Stage1SoilEvapLimit

    Layer available water (C++ SoilWater_rel_wp[i] + AirDryLimit_rel_wp[i]):
        Layer 0: sw[0] - airdry_mm  (= paw0_ad below)
        Layer 1: sw[1] - midpoint   (= paw1_ad below, midpoint = airdry+0.5*(ll-airdry))
    """
    if eos <= 0:
        return 0.0, sumes1, sumes2, t_since_wet

    sse1 = sumes1
    sse2 = sumes2

    # Layer available water — matches C++ SoilWater_rel_wp[i] + AirDryLimit_rel_wp[i]
    # Layer 0: sw[0] - ll + (ll - airdry) = sw[0] - airdry
    avail_l1 = max(0.0, sw[0] - layers[0].airdry_mm)
    if len(layers) > 1:
        l2_mid   = layers[1].airdry_mm + 0.5 * (layers[1].ll_mm - layers[1].airdry_mm)
        avail_l2 = max(0.0, sw[1] - l2_mid)
    else:
        avail_l2 = 0.0

    se1 = 0.0; se2 = 0.0; se21 = 0.0; se22 = 0.0

    # ── Test for Stage I drying (C++ lines 1197-1240) ────────────────────────
    if sse1 < u:
        # Stage I: se1 = min(eos, U - sse1), limited by layer 0 available water
        se1  = min(eos, u - sse1)
        se1  = max(0.0, min(se1, avail_l1))
        sse1 += se1

        # If eos not satisfied by Stage I, calc some Stage II
        if eos > se1:
            dsr = (sse2 / cona) ** 2 if (sse2 > 0 and cona > 0) else 0.0
            if sse2 > 0:
                # C++ line 1218: se2 = min(eos-se1, Cona*sqrt(dsr) - sse2)
                se2 = min(eos - se1, cona * (dsr ** 0.5) - sse2)
                se2 = max(0.0, se2)
            else:
                # Transition constant 0.6 (C++ line 1221)
                se2 = 0.6 * (eos - se1)
                se2 = max(0.0, se2)

            # Distribute se2 across layers 1 and 2 (C++ lines 1228-1236)
            se21 = max(0.0, min(se2, avail_l1))
            se22 = max(0.0, min(se2 - se21, avail_l2))
            se2  = se21 + se22
            sse1 = u          # Stage I now complete
            sse2 += se2
            if cona > 0:
                t_since_wet = (sse2 / cona) ** 2   # update dsr

    else:
        # ── Full Stage II (C++ lines 1242-1258) ──────────────────────────────
        # Already past Stage I: sse1 >= U
        sse1 = u
        t_since_wet += 1.0   # dsr += 1
        # se2 = min(eos, Cona*sqrt(dsr) - sse2)
        se2  = max(0.0, min(eos, cona * (t_since_wet ** 0.5) - sse2))
        se21 = max(0.0, min(se2, avail_l1))
        se22 = max(0.0, min(se2 - se21, avail_l2))
        se2  = se21 + se22
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

def calc_transpiration(sw, layers, ep, root_depth_mm, sw_prop_no_stress=0.2):
    """
    Transpiration extraction with whole-profile stress factor.

    Step 1 — Profile stress factor (simple, agronomically sound):
        PAW_ratio = total PAW in root zone / max PAW in root zone
        if PAW_ratio >= sw_prop_no_stress:  ep_stress = ep (no reduction)
        else:                               ep_stress = ep * PAW_ratio / sw_prop_no_stress
      This applies a single multiplier to potential transpiration before distribution,
      so stress in one layer reduces total T rather than being compensated by deeper layers.
      Crops meter out water more gradually as the whole profile dries.

    Step 2 — Distribute ep_stress across rooted layers using density weighting
      (HowLeaky-Core _CustomHowLeakyEngine_VegModule.CalculateTranspiration):
      - density[i]: 1.0 for layers ≤300mm, declines to 0.5 at RootDepth
      - Extraction limited to water above LL per layer
    """
    sw = sw.copy()
    if ep <= 0 or root_depth_mm <= 0:
        return 0.0, sw

    n = len(layers)

    # ── Step 1: Whole-profile stress factor ──────────────────────────────────
    # Compute PAW in root zone vs maximum PAW in root zone
    cum = 0.0
    paw_actual = 0.0
    paw_max    = 0.0
    for i, l in enumerate(layers):
        cum_prev = cum
        cum += l.thickness
        root_frac = min(1.0, max(root_depth_mm - cum_prev, 0.0) / l.thickness) if l.thickness > 0 else 0.0
        paw_actual += max(0.0, sw[i] - l.ll_mm) * root_frac
        paw_max    += (l.dul_mm - l.ll_mm) * root_frac

    if paw_max > 0 and sw_prop_no_stress > 0:
        paw_ratio = paw_actual / paw_max
        stress    = min(1.0, paw_ratio / sw_prop_no_stress)
    else:
        stress = 1.0
    ep = ep * stress   # reduce potential transpiration by stress factor

    # ── Step 2: Distribute across layers using density weighting ─────────────
    # MCFC = soil water as fraction of DUL above WP (per-layer supply, now secondary)
    mcfc = []
    for i, l in enumerate(layers):
        dul_rel = l.dul_mm - l.ll_mm
        if dul_rel > 0:
            mcfc.append(max(0.0, min(1.0, (sw[i] - l.ll_mm) / dul_rel)))
        else:
            mcfc.append(0.0)

    # Supply factor — still used for layer distribution weighting
    supply = []
    for i in range(n):
        if mcfc[i] >= sw_prop_no_stress:
            supply.append(1.0)
        else:
            supply.append(mcfc[i] / sw_prop_no_stress if sw_prop_no_stress > 0 else 0.0)

    # Root penetration and density per layer
    # depth[i] = cumulative depth to BOTTOM of layer i
    # depth[0] = 0 (top of profile) ... Depth[i+1] = bottom of layer i
    depth = [0.0]
    for l in layers:
        depth.append(depth[-1] + l.thickness)

    root_penetration = [0.0] * n
    density          = [0.0] * n
    root_penetration[0] = 1.0
    density[0]          = 1.0
    for i in range(1, n):
        layer_thick = depth[i+1] - depth[i] if i+1 < len(depth) else layers[i].thickness
        if layer_thick > 0:
            root_penetration[i] = min(1.0, max(root_depth_mm - depth[i], 0.0) / layer_thick)
        else:
            root_penetration[i] = 0.0

        if depth[i+1] > 300 if i+1 < len(depth) else depth[-1] > 300:
            bottom = depth[i+1] if i+1 < len(depth) else depth[-1]
            if root_depth_mm > 300:
                density[i] = max(0.0, 1.0 - 0.5 * min(1.0, (bottom - 300.0) / (root_depth_mm - 300.0)))
            else:
                density[i] = 0.5
        else:
            density[i] = 1.0

    # Layer transpiration demand
    layer_transp = [0.0] * n
    psup = 0.0
    for i in range(n):
        if root_penetration[i] < 1.0 and mcfc[i] <= (1.0 - root_penetration[i]):
            layer_transp[i] = 0.0
        else:
            layer_transp[i] = density[i] * supply[i] * ep
        # Limit to available water above LL
        sw_rel_wp = max(0.0, sw[i] - layers[i].ll_mm)
        layer_transp[i] = min(layer_transp[i], sw_rel_wp)
        psup += layer_transp[i]

    # Scale down if total supply exceeds potential
    if psup > ep and psup > 0:
        scale = ep / psup
        for i in range(n):
            layer_transp[i] *= scale

    # Extract from soil — limit to available water above LL
    actual_transp = 0.0
    for i in range(n):
        avail_i = max(0.0, sw[i] - layers[i].ll_mm)
        extract  = max(0.0, min(layer_transp[i], avail_i))
        sw[i]   -= extract
        # Do NOT floor at ll_mm here — sw may already be below ll from evaporation
        # and we must not restore it. Just ensure we didn't go below what was available.
        actual_transp += extract

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
    # HowLeaky: eos = pan_evap * (1 - total_cover * 0.87)  (eq 3-25)
    eos = pet * (1.0 - green_cover * 0.87)
    eos = max(0.0, eos)
    # HowLeaky CalculatePotentialTranspiration:
    #   ep = min(GreenCover * PanEvap, PanEvap - SoilEvap)
    # Here we use green_cover for ep (total_cover is passed for eos reduction)
    # The (PanEvap - SoilEvap) cap is applied after actual soil evap is known;
    # for now use green proportion of pan evap
    ep  = pet * green_cover
    ep  = max(0.0, ep)
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

def model_soil_cracking(sw, layers, rain, max_infilt):
    """
    HowLeakyEngine::ModelSoilCracking() — exact port of C++ source lines 920-988.

    When the top 2 layers are < 30% of DUL, cracks allow rain to bypass
    the surface and enter deeper layers directly. This:
      - Reduces effective rain available for runoff
      - Directly recharges lower layers

    Returns (effective_rain, crack_additions) where crack_additions[i] is
    the water (mm) added directly to layer i through cracks.
    """
    if max_infilt <= 0 or rain < 0.1:
        return rain, [0.0] * len(layers)

    n = len(layers)
    red = [0.0] * n

    # mcfc = soil water as fraction of DUL (relative to WP) per layer
    mcfc = []
    for i, l in enumerate(layers):
        dul_rel = l.dul_mm - l.ll_mm   # DrainUpperLimit_rel_wp
        if dul_rel > 0:
            sw_rel = sw[i] - l.ll_mm   # SoilWater_rel_wp
            mcfc.append(max(0.0, min(1.0, sw_rel / dul_rel)))
        else:
            mcfc.append(0.0)

    # Cracks only if top 2 layers are < 30% of DUL
    if mcfc[0] >= 0.3 or (len(mcfc) > 1 and mcfc[1] >= 0.3):
        return rain, red

    # Number of layers cracks extend to
    nod = 1
    for i in range(1, n):
        if mcfc[i] >= 0.3:
            break
        nod += 1

    # Fill cracks from lowest cracked layer first
    # Each layer can receive up to 50% of its DUL above current SW
    tred = min(max_infilt, rain)
    for i in range(nod - 1, -1, -1):
        l = layers[i]
        dul_rel = l.dul_mm - l.ll_mm
        sw_rel  = sw[i] - l.ll_mm
        space   = max(0.0, dul_rel / 2.0 - sw_rel)
        red[i]  = min(tred, space)
        tred   -= red[i]
        if tred <= 0:
            break

    # Effective rain: replace crack amount with layer 0 crack amount
    # (layer 0 cracks are counted differently — bypass the surface entirely)
    eff_rain = rain + red[0] - min(max_infilt, rain)
    eff_rain = max(0.0, eff_rain)
    red[0] = 0.0   # layer 0 crack water already accounted in effective_rain reduction

    return eff_rain, red


def daily_water_balance(
        sw, layers, soil,
        rain, epan,
        green_cover, total_cover, root_depth_mm, crop_factor,
        sumes1, sumes2, t_since_wet,
        tillage_cn_reduction=0.0,
        sw_prop_no_stress=None,
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

    # -- 0. Soil cracking — reduces effective rain, recharges deep layers -----
    # HowLeaky ModelSoilCracking(): when top layers < 30% DUL, rain bypasses
    # surface and enters cracks directly, reducing effective rain for runoff.
    _max_crack = getattr(soil, 'crack_infil', 0.0)
    eff_rain, crack_red = model_soil_cracking(sw, layers, rain, _max_crack)
    # Apply crack additions directly to layers (bypasses runoff/infiltration)
    for _i, _cr in enumerate(crack_red):
        if _cr > 0:
            sw[_i] = min(sw[_i] + _cr, layers[_i].sat_mm)

    # -- 1. Runoff — uses TOTAL cover (green + residue) -----------------------
    # HowLeaky applies AMC on ALL days including bare/fallow (no crop condition).
    # PERFECT option (C++ source lines 1065-1074):
    #   sumh20 = Σ wf[i] * max(SoilWater_rel_wp[i],0) / SaturationLimit_rel_wp[i]
    #   where SoilWater_rel_wp[i] = sw[i] - ll[i]  (relative to WP)
    #         SaturationLimit_rel_wp[i] = sat[i] - ll[i]
    #   Loop over LayerCount-1 (all layers except the last)
    # S = int(smx * (1 - sumh20))  — integer cast as per C++ source
    depth_max = layers[-1].depth_mm
    sumh20 = 0.0
    for i, l in enumerate(layers[:-1]):   # LayerCount-1 as per C++ source
        depth_i  = layers[i-1].depth_mm if i > 0 else 0.0
        depth_i1 = l.depth_mm
        wfi = 1.016 * (np.exp(-4.16 * depth_i  / depth_max) -
                       np.exp(-4.16 * depth_i1 / depth_max))
        sw_rel_wp  = max(0.0, sw[i] - l.ll_mm)
        sat_rel_wp = max(0.001, l.sat_mm - l.ll_mm)
        sumh20 += wfi * sw_rel_wp / sat_rel_wp
    sw_ratio = max(0.0, min(1.0, sumh20))

    # Capture effective CN and S for diagnostic output
    _cn1, _cn2_eff, _cn3 = calc_cn(soil.cn2_bare, total_cover,
                                     soil.cn_cover_reduction, tillage_cn_reduction)
    _smx  = 254.0 * (100.0 / max(_cn1, 1.0) - 1.0)
    _sh20 = max(0.0, min(1.0, sw_ratio)) if sw_ratio is not None else None
    _S    = float(int(_smx * (1.0 - _sh20))) if _sh20 is not None else 254.0*(100.0/_cn2_eff-1.0)
    _S    = max(0.0, _S)
    _cn_eff = 25400.0 / (_S + 254.0) if _S > 0 else _cn2_eff
    runoff = calc_runoff(eff_rain, soil.cn2_bare, total_cover,
                         soil.cn_cover_reduction, tillage_cn_reduction,
                         sw_ratio=sw_ratio)
    infil = max(0.0, eff_rain - runoff)

    # -- 2. Reset evap accumulators if significant rain -----------------------
    # Reset evap accumulators based on actual infiltration (rain - runoff)
    _infil_for_reset = max(0.0, rain - runoff)
    sumes1, sumes2, t_since_wet = reset_evap_accumulators(
        rain, sumes1, sumes2, t_since_wet, soil.u, infil_mm=_infil_for_reset)

    # -- 3-6. Exact HowLeaky UpdateWaterBalance seepage loop ------------------
    # HL: CalculateSoilEvap() and CalculateTranspiration() compute amounts only.
    # UpdateWaterBalance() then applies everything in one seepage loop:
    #   SW[i] += Seepage[i] - Evap[i] - Transp[i]
    #   if SW[i] > DUL: drain = swcon*(SW[i]-DUL); SW[i] -= drain
    #   if SW[i] > SAT: overflow back up

    eos, ep = partition_et(epan, total_cover, crop_factor)

    # Step A: compute soil evap amounts (accumulators updated, amounts not yet applied)
    es, sumes1, sumes2, t_since_wet = calc_soil_evap(
        sw, layers, eos, soil.u, soil.cona, sumes1, sumes2, t_since_wet)
    l2_floor = (layers[1].airdry_mm + 0.5*(layers[1].ll_mm - layers[1].airdry_mm)
                if len(layers) > 1 else 0.0)
    avail_l1 = max(0.0, sw[0] - layers[0].airdry_mm)
    avail_l2 = max(0.0, sw[1] - l2_floor) if len(layers) > 1 else 0.0
    take_l1  = min(es, avail_l1)
    take_l2  = min(es - take_l1, avail_l2)
    es       = take_l1 + take_l2          # actual es (se1 + se2)
    se22     = take_l2                     # layer-1 evap component

    # Step B: compute transpiration per layer (not yet applied)
    ep_actual = ep
    _, sw_t = calc_transpiration(sw, layers, ep_actual, root_depth_mm,
                                  sw_prop_no_stress=(sw_prop_no_stress if sw_prop_no_stress is not None else getattr(soil,'sw_prop_no_stress',0.2)))
    lt = np.maximum(0.0, sw - sw_t)       # LayerTranspiration[i]

    # Step C: UpdateWaterBalance seepage loop (exact C# port)
    # SW[i] += Seepage[i] - ET[i]; drain if > DUL; overflow if > SAT
    drain    = infil          # starts as infiltration entering layer 0
    overflow = 0.0

    for i, layer in enumerate(layers):
        # C#: SoilWaterRelWP[i] += Seepage[i] - ET[i]
        if i == 0:
            sw[i] += drain - (es - se22) - lt[i]     # se1 goes to layer 0
            sw[i]  = max(sw[i], layer.airdry_mm)
        elif i == 1:
            sw[i] += drain - lt[i] - se22             # se22 goes to layer 1
            sw[i]  = max(sw[i], l2_floor)
        else:
            sw[i] += drain - lt[i]
            sw[i]  = max(sw[i], layer.ll_mm)

        # Drain only if above DUL
        if sw[i] > layer.dul_mm:
            ksat_day = layer.ksat * 24.0
            sat_dul  = max(0.001, layer.sat_mm - layer.dul_mm)
            swcon    = 2.0*ksat_day/(sat_dul+ksat_day) if (sat_dul+ksat_day)>0 else 1.0
            excess   = sw[i] - layer.dul_mm
            drain    = min(swcon * excess, ksat_day)
            if drain < 0: drain = 0.0
            sw[i]   -= drain
        else:
            drain = 0.0

        # Cap at SAT → overflow cascades back up
        if sw[i] > layer.sat_mm:
            oflow  = sw[i] - layer.sat_mm
            sw[i]  = layer.sat_mm
            j = i - 1
            while oflow > 0 and j >= 0:
                space = layers[j].sat_mm - sw[j]
                take  = min(oflow, space)
                sw[j] += take
                oflow -= take
                j     -= 1
            overflow += max(0.0, oflow)

    deep_drain = drain
    transp     = float(lt.sum())

    runoff += overflow
    infil  -= overflow

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
        'cn2_eff'     : round(_cn_eff, 1),    # effective CN incorporating cover AND soil water
        'cn2_cover'   : round(_cn2_eff, 1),  # CN2 after cover reduction only
        'S_value'     : round(_S, 1),         # actual retention S used for runoff (mm)
        'sumh20'      : round(_sh20, 3) if _sh20 is not None else 0.0,
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
