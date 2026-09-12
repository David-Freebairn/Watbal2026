# Waterbal2026 — Water Balance Model Environment

A Python/Streamlit re-implementation of the PERFECT and HowLeaky water balance
models, built for dryland cropping research in Australia.

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Folder structure

```
Watbal2026/
  app.py                    ← Home page: climate source selection + SILO map
  requirements.txt
  README.md
  core/                     ← Model engine and utilities
    waterbalance.py         ← Daily water balance (runoff, evap, transp, drainage, erosion)
    run_simulation.py       ← Simulation runner, annual/monthly stats, yield
    soil.py                 ← SoilProfile dataclass, reads PERFECT .PRM files
    soil_xml.py             ← Reads HowLeaky .soil and .xml files
    soil_excel.py           ← Reads Excel soil description files
    vege.py                 ← Reads HowLeaky .vege files, fractional cover model
    cover_excel.py          ← Reads Excel cover schedules
    read_p51.py             ← Reads SILO P51 climate files (comma or whitespace format)
    silo.py                 ← SILO API: station search, fetch, parquet cache
    reliability.py          ← Station reliability lookup from CSV
    output_chart.py         ← Full PNG water balance output chart
    input_summaries.py      ← Soil and vegetation parameter summary graphics
    nav.py / styles.py      ← Shared Streamlit helpers
  pages/
    1_Run_simulation.py     ← Single or multi-scenario simulation
    2_Multi_scenario.py     ← Multi-scenario management and results table
    3_Monthly_averages.py   ← Long-term monthly climate averages
    4_Results.py            ← Browse and download saved results
  data/
    silo_reliability.csv    ← ~7,950 station reliability ratings
  Soils/                    ← Your .soil, .xml, .PRM soil files
  Vegetation descriptions/  ← Your .vege and Excel cover schedule files
  Climate files/            ← Your .P51 climate files
  results/                  ← Auto-created, saves all run outputs
```

## How it works

**Home page** — choose climate source:
- **SILO station**: search by name or pick from the reliability map
  (green = ≥90% observed, amber = 50–89%, red = <50%)
- **Local P51 file**: picks from files in `Climate files/`

**Run simulation** — configure and run:
- Single: 1 soil × 1 vegetation → monthly chart + annual table
- Multiple: N soils × M vegetation → all combinations → summary table,
  click any row for the individual monthly chart

**Results persist** to `results/multi_scenario_results.json` between sessions.

## Key model features

- **Runoff**: SCS curve number with AMC (antecedent moisture) adjustment
- **Infiltration**: cascade through layers with Ksat limit; saturation overflow
  correctly routed back to runoff (not lost)
- **Soil evaporation**: Ritchie two-stage (Stage I = U mm, Stage II = Cona√t)
- **Transpiration**: proportional extraction from rooted layers above WP
- **Erosion**: PERFECT sediment concentration model (t/ha/day)
- **Yield**: transpiration × TUE × harvest index (t/ha/season)
- **Cover**: fractional model — `green% + (1−green%) × residue%`
- **Water balance**: Rain = Runoff + Evap + Transpiration + Drainage + ΔSW
  (balance error = 0.000 mm verified)

## Climate files (P51 format)

Download from https://www.longpaddock.qld.gov.au/silo/ → Point Data.
Both whitespace-separated (classic) and comma-separated (modern) formats supported.

## References

Littleboy et al. (1992) PERFECT QB92005, DAQ.
Ritchie JT (1972) Water Resources Research 8(5):1204–1213.
SCS (1972) National Engineering Handbook Section 4.
