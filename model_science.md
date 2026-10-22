# Watbal2026 — Model Science Documentation

## Daily Simulation Order

Each day is processed in this exact sequence, matching HowLeaky's `SimulateDay()` order:

```
1. Load climate data (rain, pan evaporation)
2. Calculate runoff (CN method with AMC)
3. Calculate soil evaporation (amounts only, not yet applied)
4. Calculate transpiration (amounts only, not yet applied)
5. UpdateWaterBalance seepage loop:
      for each layer top→bottom:
          SW[i] += infiltration − ET[i]
          if SW[i] > DUL: drain; carry to next layer
          if SW[i] > SAT: overflow back to surface
6. Record outputs
```

---

## Runoff

### Curve Number method

The SCS curve number method estimates daily runoff from rainfall:

```
Q = (P − Ia)² / (P − Ia + S)    when P > Ia
Q = 0                             when P ≤ Ia

Ia = 0.2 × S     (initial abstraction)
S  = retention parameter (mm)
```

### Antecedent moisture condition (AMC)

S is adjusted for soil wetness using a layer-weighted index `sumh20`:

```
sumh20 = Σ wf[i] × max(SW[i] − LL[i], 0) / (SAT[i] − LL[i])

wf[i] = 1.016 × (exp(−4.16 × depth_top/depth_max) 
                − exp(−4.16 × depth_bot/depth_max))
```

Only the top (n−1) layers are included (HowLeaky source, line 1066).

`sumh20` ranges from 0 (all layers at LL) to ~0.6 (all layers at DUL) because the denominator is (SAT−LL), not (DUL−LL).

**S calculation** (`amc_retention()`):

```
smx = 254 × (100/CN1 − 1)            # S at CN1 (dry profile)
S   = int(smx × (1 − sumh20))                  # amc_mode = "howleaky"
S   = min(S(CN2), int(smx × (1 − sumh20)))     # amc_mode = "capped" (default)
```

The "howleaky" form follows HowLeaky/CREAMS: a dry profile produces less runoff than CN2 and a wet one more. The capped form limits S to S(CN2), so wetness can only add runoff. With the CN1 formula below, `smx × (1 − sumh20)` stays above S(CN2) until the profile is wetter than DUL, so the capped form behaves as a near-constant curve number. Against the Wallumbilla tilled and minimum-till catchments (1983–1997) the uncapped form fitted runoff clearly better (pooled 3-day NSE 0.72 uncapped, at most about 0.60 capped). Large storms on wet profiles were under-predicted by about half with the cap. Select it per soil with `<AMCMode>howleaky</AMCMode>` in the .soil/.xml file (or `profile.amc_mode`). The default remains "capped" because CN2 values in existing soil files were tuned for it. Under "howleaky" the same CN2 gives much less runoff: about 6–7 CN units higher was needed at Wallumbilla (CN2 83 → 90).

### CN1 and CN3

As implemented in `calc_cn()`:

```
CN1 = CN2 / (2.383 − 0.0123 × CN2)
CN3 = CN2 × exp(0.00673 × (100 − CN2))
```

(Earlier versions of this document quoted the CREAMS polynomial CN1 = −16.91 + 1.348·CN2 − 0.01379·CN2² + 0.0001177·CN2³. The code has never used it.)

### Cover reduction

```
CN2_effective = CN2_bare − cover_fraction × CN_reduction_at_full_cover
cover_fraction = green_cover + residue_cover × (1 − green_cover)
```

---

## Soil Evaporation

Ritchie (1972) two-stage model, ported from `HowLeakyEngine.cpp CalculateSoilEvaporation()`.

### Potential soil evaporation

```
eos = pan_evap × (1 − total_cover × 0.87)
total_cover = residue + (1 − residue) × green      (fractions)
```

Potential soil evaporation and the runoff curve number respond to **total** cover. Potential transpiration responds to **green** cover only (see below). Before Oct 2026 transpiration demand also used total cover, so stubble transpired whenever roots were present.

### Stage 1 (energy-limited)

While `sse1 < U` (Stage 1 cumulative evap < limit):

```
se1 = min(eos, U − sse1)
se1 = min(se1, SW[0] − airdry[0])     (limited by Layer 1 water)
sse1 += se1
```

If eos not satisfied by Stage 1, some Stage 2 evaporation occurs on the same day.

Setting `U = 0` skips Stage 1 entirely. This removes one parameter and is a defensible simplification, particularly for cracking clay soils.

### Stage 2 (diffusion-limited)

Once `sse1 ≥ U`:

```
dsr  += 1                              (days since rain — increments daily)
se2   = min(eos, Cona × √dsr − sse2)
sse2 += se2
```

### Reset on infiltration

When rain infiltrates, accumulators are reset:

```
sse2 = max(0, sse2 − max(0, infil − sse1))
sse1 = max(0, sse1 − infil)
```

### Layer extraction

- **Layer 0:** extracted down to airdry limit
- **Layer 1:** extracted down to midpoint of (airdry, LL)
- **Deeper layers:** not directly evaporated

---

## Transpiration

### Potential transpiration

```
ep = pan_evap × green_cover
```

### Step 1: Whole-profile stress factor

Before distributing demand across layers, a profile-level stress factor is applied:

```
PAW_actual = Σ max(SW[i] − LL[i], 0) × root_fraction[i]
PAW_max    = Σ (DUL[i] − LL[i]) × root_fraction[i]
PAW_ratio  = PAW_actual / PAW_max

stress     = min(1.0, PAW_ratio / sw_prop_no_stress)
ep_actual  = ep × stress
```

`sw_prop_no_stress` is the PAWC fraction below which transpiration begins to reduce. It is set in the vegetation file (default 0.2; values of 0.3–0.5 give more realistic late-season behaviour).

This prevents the common problem of profiles depleting completely before crop maturity. Water saved by stress reduction goes to drainage rather than being permanently withheld.

### Step 2: Layer distribution

Ported from `_CustomHowLeakyEngine_VegModule.CalculateTranspiration()`:

**Root penetration** (fraction of layer within root zone):
```
root_penetration[0] = 1.0
root_penetration[i] = min(1.0, max(RootDepth − depth_top[i], 0) / thickness[i])
```

**Density** (extraction efficiency by depth):
```
density[i] = 1.0                                    for depth_bot[i] ≤ 300mm
density[i] = 1.0 − 0.5 × (depth_bot−300)/(RootDepth−300)   for deeper layers
density[i] ≥ 0.0
```

**Supply** (availability factor):
```
MCFC[i]    = max(0, SW[i]−LL[i]) / max(0.001, DUL[i]−LL[i])
supply[i]  = 1.0          if MCFC[i] ≥ sw_prop_no_stress
           = MCFC[i] / sw_prop_no_stress   otherwise
```

**Layer demand:**
```
LayerT[i] = density[i] × supply[i] × ep_actual
LayerT[i] = min(LayerT[i], SW[i] − LL[i])    (can't extract below LL)
```

If `Σ LayerT[i] > ep_actual`, all layers scaled down proportionally.

### Root depth

Root depth is held at the seasonal maximum while green cover > 1%, preventing premature cutoff before harvest. It only declines once green cover begins to fall.

---

## Drainage

### SWCON approach (HowLeaky)

For each layer with SW > DUL:

```
swcon = 2 × ksat / (SAT − DUL + ksat)
drain = min(swcon × (SW − DUL), ksat)
SW   -= drain
```

`ksat` is the MaxDailyDrainRate from the soil file (mm/day), converted to mm/hr internally.

Drainage only occurs when SW exceeds DUL. It cascades layer-by-layer downward. Water draining from the bottom layer leaves the profile as deep drainage.

### Saturation overflow

If SW exceeds SAT after receiving seepage, overflow cascades back up through layers until it either fills available space or reaches the surface as runoff.

---

## Erosion

Modified Universal Soil Loss Equation (MUSLE, Williams 1975):

```
SoilLoss = 11.8 × (Runoff × Qpeak)^0.56 × K × LS × C × P

Qpeak = Runoff × drainage_area / (3.6 × event_duration)
LS    = computed from slope and slope length
C     = cover factor = exp(−2.996 × total_cover)
P     = practice factor (1.0 for no conservation practice)
K     = soil erodibility factor (from soil file)
```

---

## Vegetation schedule (CropFactorMatrix)

Each row is DOY (x), green cover % (y), **residue** cover % (z), root depth mm (a). Total cover is combined as fractions:

```
total = residue + (1 − residue) × green
```

Total cover drives the curve number, erosion and potential soil evaporation. Green cover alone drives potential transpiration. (Before Oct 2026 the z column was read as total cover and green was subtracted from it.)

**Crop season:** from PanPlantDay for DaysPlantingToHarvest days. Within it, root depth is held at the season's maximum so roots do not retreat before harvest. Season transpiration accumulates for yield, and harvest is the day the season ends. Green cover outside the season (fallow weeds) transpires using its own root depth but is not counted as crop. If a schedule has no plant day, or a season length of a year or more (many files carry 10000), the older rule applies: the season runs while green cover > 1 %.

## Yield

Cover model biomass accumulation (daily):

```
dry_matter += TUE × transpiration[day]   (kg/ha)
```

At harvest:

```
yield = HI × dry_matter / 1000   (t/ha)
```

Note: the Cover model does not divide by 10 at harvest (unlike the LAI model).

Dry matter resets at planting. A safety harvest occurs at year boundary if green cover has not reached zero during the year.

---

## Water Balance Check

Evaporation and transpiration are computed from the same start-of-day soil water. When together they would take a layer below its floor (air-dry for layer 1, midway AD–LL for layer 2, LL below), the excess is trimmed from transpiration first, then soil evaporation. The reported fluxes always equal the water removed. (Before Oct 2026 the layer was simply reset to its floor, which created water. The soil cracking routine, since removed, also lost rain it could not place in cracks.)

The daily balance is verified by:

```
ΔSW = Rain − Runoff − Soil_Evap − Transpiration − Drainage
```

An annual balance error < 0.01 mm/yr confirms numerical integrity. This is reported in the Run summaries tab.

---

## Input Parameter Summary

### Soil file parameters

| Parameter | Symbol | Units | Typical range |
|-----------|--------|-------|---------------|
| Layer depths (cumulative) | — | mm | — |
| Air dry moisture | AD | %Vol | 8–25 |
| Wilting point | LL (WP) | %Vol | 15–35 |
| Field capacity | DUL (FC) | %Vol | 30–55 |
| Saturation | SAT | %Vol | 45–65 |
| Max drainage rate | ksat | mm/day | 5–200 |
| Stage 1 evap limit | U | mm | 0–6 |
| Stage 2 evap coeff | Cona | mm/day^0.5 | 2.5–5.5 |
| Runoff curve number | CN2 | — | 65–95 |
| CN cover reduction | — | CN units | 10–20 |
| Erodibility | K | metric | 0.1–0.6 |
| Slope | S | % | 1–15 |
| Slope length | L | m | 20–200 |

### Vegetation file parameters

| Parameter | Symbol | Notes |
|-----------|--------|-------|
| Green cover schedule | — | % by DOY (up to 14 points) |
| Residue cover schedule | — | % by DOY |
| Root depth schedule | — | mm by DOY |
| Transpiration efficiency | TUE | kg/ha per mm transpiration |
| Harvest index | HI | fraction 0–1 |
| PAW no-stress threshold | sw_prop | fraction of PAWC; 0.2–0.5 recommended |
| Cover multipliers | — | for calibration without editing schedule |

---

## Differences from HowLeaky

| Feature | HowLeaky | Watbal2026 | Notes |
|---------|----------|-----------|-------|
| AMC | S from CN1 | Cap at S(CN2) by default; HowLeaky form per soil | `<AMCMode>howleaky</AMCMode>`; CN2 ≈ 6–7 higher |
| Potential transpiration | Green cover | Green cover (fixed Oct 2026; was total cover) | Soil evaporation and CN use total cover |
| Transpiration stress | Per-layer supply only | Profile factor + per-layer | More gradual seasonal drawdown |
| Root extraction rate | — | Optional `kl` (fraction of layer PAW per day) | `profile.kl`; off by default |
| Soil cracking | Optional | Removed (Oct 2026) | Rarely mattered; `<SoilCrack>` is ignored |
| Irrigation | Yes | No | Future development |
