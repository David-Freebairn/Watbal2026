# Watbal2026 — Water Balance Model Environment

A Python/Streamlit reimplementation of the PERFECT/HowLeaky soil water balance model for dryland cropping research in semi-arid Australia.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/streamlit-1.35+-red.svg)](https://streamlit.io/)

---

## Overview

Watbal2026 simulates daily soil water balance for dryland cropping systems, enabling comparison of water balance components, runoff, drainage, and crop yield across different soils, vegetation types, and climates.

It is intended as a research and teaching tool — not a replacement for HowLeaky — but aims to reproduce its core water balance physics faithfully enough for practical use.

**Key features:**
- Daily water balance: runoff, soil evaporation, transpiration, drainage
- SILO climate data (Australian Bureau of Meteorology) or local P51 files
- Excel-format soil and vegetation files compatible with HowLeaky's input structure
- Legacy HowLeaky `.soil` (XML) and `.vege` file support
- Annual and daily output summaries with interactive charts
- Multi-scenario comparison (multiple soils × vegetation combinations)
- Erosion (MUSLE) and crop yield estimation

---

## Installation

```bash
git clone https://github.com/David-Freebairn/Watbal2026
cd Watbal2026
pip install -r requirements.txt
streamlit run app.py
```

**Requirements:** Python 3.11+, see `requirements.txt`

---

## Usage

### 1. Select climate (Start tab)
- **SILO station** — search by name or pick from map; downloads data automatically
- **Local P51 file** — select from `Climate files/` folder
- Set simulation start and end years

### 2. Set up scenarios (Simulate tab)
- **Single** — one soil × one vegetation combination
- **Multiple** — any combination of soils and vegetation files

### 3. View results
- **Run summaries** — annual water balance table with statistics
- **Daily outputs** — interactive daily trace with zoom and variable selection
- **Climate summary** — monthly climate averages

---

## Input Files

### Soil files

**Excel format** (recommended, `.xlsx`) — place in `Soils/` folder:

| Row | Parameter | Units |
|-----|-----------|-------|
| Soil name | — | — |
| Number of Horizons | count | — |
| Layer Depth (Cumulative) | mm | to bottom of each layer |
| Air dry moisture (AD) | %Vol | |
| Wilting point (WP) | %Vol | lower limit for transpiration |
| Field capacity (FC) | %Vol | drainage upper limit |
| Sat. water content (Sat) | %Vol | |
| Max. drainage from layer | mm/day | hydraulic conductivity |
| Stage 1 evap. (U) | mm | Stage 1 soil evap limit (0 = skip Stage 1) |
| Stage 2 evap. (Cona) | mm/day^0.5 | controls Stage 2 drying rate |
| Runoff Curve Number (CN) | CN units | bare soil |
| CN reduction cover | CN units | reduction at 100% cover |
| Erodibility (K) | metric | MUSLE K factor |
| Field Slope (S) | % | |
| Slope Length (L) | m | |

**HowLeaky XML format** (`.soil`) — also supported.

### Vegetation files

**Excel format** (recommended, `.xlsx`) — place in `Vegetation descriptions/` folder:

The schedule defines green cover %, residue cover %, and root depth (mm) for up to 14 time points per year. Values are linearly interpolated between points.

| Parameter | Units | Notes |
|-----------|-------|-------|
| Plant day | Julian day | |
| Days to harvest | days | |
| Transpiration efficiency (TUE) | kg/ha/mm | dry matter per mm transpiration |
| Harvest index (HI) | fraction | yield = transpiration × TUE × HI / 1000 |
| PAW no-stress threshold | fraction | transpiration reduces below this PAWC fraction |
| Green cover multiplier | — | scales schedule values for calibration |
| Residue cover multiplier | — | |
| Root depth multiplier | — | |

**HowLeaky `.vege` format** — also supported.

### Climate files

**P51 format** — place in `Climate files/` folder. Standard SILO P51 export format.

**SILO API** — downloads automatically; requires internet connection.

---

## Model Science

### Water Balance

Daily water balance equation:

```
ΔSW = Rain − Runoff − Soil_Evap − Transpiration − Drainage
```

Balance error is reported in the Run summaries tab. A well-calibrated simulation should show < 0.01 mm/yr error.

### Runoff (SCS Curve Number)

Runoff uses the SCS curve number method with antecedent moisture condition (AMC) adjustment:

```
S  = S(CN2) − sumh20 × (S(CN2) − S(CN3))
Ia = 0.2 × S
Q  = (Rain − Ia)² / (Rain − Ia + S)   when Rain > Ia
```

Where `sumh20` is a layer-weighted soil wetness index (0=dry, 1=saturated):

```
sumh20 = Σ wf[i] × max(sw[i]−LL[i], 0) / (SAT[i]−LL[i])
wf[i]  = 1.016 × (exp(−4.16×d_top/d_max) − exp(−4.16×d_bot/d_max))
```

**Key design decision:** CN2 (user input) is anchored as the dry/normal baseline. Soil wetness can only *increase* runoff above the CN2 baseline, not decrease it below. This preserves CN2 as the physically meaningful input.

CN is reduced by total cover (green + residue) using the cover reduction parameter from the soil file.

### Soil Evaporation

Ritchie (1972) two-stage model:

- **Stage 1** (energy limited): evaporates at potential rate until `sse1 = U`
- **Stage 2** (diffusion limited): `Es = Cona × sqrt(t) − sse2`

Setting U=0 skips Stage 1, going directly to the power-law Stage 2 drying curve. This removes one calibration parameter and is a legitimate choice for clay soils.

Potential soil evaporation: `eos = pan_evap × (1 − total_cover × 0.87)`

### Transpiration

Two-step calculation:

**Step 1 — Profile stress factor:**
```
PAW_ratio = PAW_in_root_zone / max_PAW_in_root_zone
stress    = min(1.0, PAW_ratio / sw_prop_no_stress)
ep_actual = ep_potential × stress
```
This reduces potential transpiration as the whole profile dries below the stress threshold, preventing unrealistic complete depletion before crop maturity.

**Step 2 — Layer distribution** (HowLeaky algorithm):
```
density[i] = 1.0                          for layers ≤ 300mm
           = 1.0 − 0.5 × (depth−300) / (RootDepth−300)   for deeper layers
supply[i]  = min(1.0, MCFC[i] / sw_prop_no_stress)
LayerT[i]  = density[i] × supply[i] × ep_actual
```
Scaled down if total demand exceeds `ep_actual`.

**Potential transpiration:** `ep = pan_evap × green_cover`

Root depth is held at the seasonal maximum while green cover > 1%, preventing premature transpiration cutoff before harvest.

### Drainage

Rate-limited using HowLeaky's swcon approach:
```
swcon = 2 × ksat / (SAT−DUL + ksat)
drain = min(swcon × excess_above_DUL, ksat)
```
Drainage only occurs when a layer exceeds DUL. Water cascades to the next layer. Saturation overflow backs up through layers to the surface.

### Erosion (MUSLE)

```
SoilLoss = 11.8 × (Runoff × Qpeak)^0.56 × K × LS × C × P
```

### Yield

```
dry_matter += TUE × transpiration   (daily)
yield = HI × dry_matter / 1000      (t/ha, at harvest)
```

---

## Calibration Guidance

### CN (Curve Number)
The CN controls runoff generation. CN2 is the soil's bare-soil runoff potential under normal antecedent moisture:
- Black Earth (Vertosol): 78–85
- Red soil (Chromosol): 70–78
- Sandy soil: 60–70

Observed 10-year mean runoff ≈ 8–12% of annual rainfall for dryland cropping in SE Queensland.

### PAW no-stress threshold
Controls how quickly transpiration reduces as the profile dries:
- `0.0` — no reduction; profile completely depleted each season (not realistic)
- `0.2` — stress starts at 20% PAWC (HowLeaky default)
- `0.3–0.5` — more realistic crop behaviour; water metered more gradually
- `0.7` — conservative; significant stress starts early

### U (Stage 1 evap limit)
- `4 mm` — HowLeaky default; good starting point
- `0 mm` — skip Stage 1; reduces parameterisation; defensible for clay soils

### Cona (Stage 2 evap coefficient)
- `3.5–4.5` — typical for clay soils in SE Queensland
- Higher values → faster Stage 2 drying

---

## Validation Status

Validated against HowLeaky V6 outputs and observed runoff/erosion data from the Greenmount trial (1976–1993), Darling Downs, Queensland.

| Component | Watbal2026 | HowLeaky | Observed |
|-----------|-----------|----------|----------|
| Rainfall | 681 mm/yr | 718 mm/yr | 718 mm/yr |
| Runoff (CN80) | ~53 mm/yr | 51 mm/yr | 53 mm/yr |
| Soil evaporation | 374 mm/yr | 397 mm/yr | — |
| Transpiration | 241 mm/yr | 273 mm/yr | — |
| Drainage | 8 mm/yr | 8 mm/yr | — |
| Erosion | ~30 t/ha/yr | 33 t/ha/yr | 34 t/ha/yr |
| Balance error | 0.00 mm/yr | — | — |

*Note: Watbal2026 requires CN80 to match HL's CN83 due to minor differences in AMC implementation.*

---

## Source References

- **HowLeaky-Core** (C# source): [github.com/HowLeaky/HowLeaky-Core](https://github.com/HowLeaky/HowLeaky-Core)
- **HowLeaky Manual V5** (2018): algorithm descriptions and equations
- **SILO climate database**: [www.longpaddock.qld.gov.au/silo](https://www.longpaddock.qld.gov.au/silo/)
- **Ritchie (1972)**: Two-stage soil evaporation model
- **USDA SCS (1972)**: Curve Number runoff method
- **CREAMS (1980)**: CN1 polynomial for antecedent moisture adjustment

---

## Project Structure

```
Watbal2026/
├── app.py                     ← Start page: climate selection
├── pages/
│   ├── 1_Simulate.py          ← Scenario setup and run
│   ├── 2_Run_summaries.py     ← Browse and compare results
│   ├── 3_Daily_outputs.py     ← Interactive daily trace
│   └── 4_Climate_summary.py   ← Monthly climate averages
├── core/
│   ├── waterbalance.py        ← Daily water balance engine
│   ├── run_simulation.py      ← Simulation runner and statistics
│   ├── soil_xml.py            ← HowLeaky .soil XML reader
│   ├── soil_excel.py          ← Excel soil file reader
│   ├── vege.py                ← HowLeaky .vege file reader
│   ├── cover_excel.py         ← Excel vegetation cover reader
│   ├── read_p51.py            ← P51 climate file reader
│   ├── silo.py                ← SILO API client
│   └── input_summaries.py     ← Soil/vege summary charts
├── data/
│   └── silo_reliability.csv   ← Station reliability data
├── Climate files/             ← Local P51 climate files
├── Soils/                     ← Soil description files
├── Vegetation descriptions/   ← Vegetation cover files
└── results/                   ← Saved simulation outputs
```

---

## Known Differences from HowLeaky

1. **CN value** — Use CN ≈ 2 units lower than HowLeaky to get equivalent runoff, due to minor AMC implementation differences.
2. **Transpiration** — Watbal2026 typically produces 10–15% less annual transpiration than HowLeaky. The `PAW no-stress threshold` parameter controls late-season drawdown.
3. **Soil cracking** — not implemented (removed from HowLeaky-Core March 2022).
4. **Irrigation** — not implemented.
5. **Tillage** — CN tillage reduction is read from soil files but not dynamically scheduled.

---

## Licence

Open source — see LICENSE file.

## Author

David Freebairn — Agricultural scientist, Queensland, Australia.

Developed with assistance from Claude (Anthropic).
