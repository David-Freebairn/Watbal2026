Watbal2026 update, October 2026 (Wallumbilla calibration)

Where each file goes in your Watbal2026 folder / GitHub repo:

  core/waterbalance.py      -> core/   (replace)
  core/soil_xml.py          -> core/   (replace)
  core/soil.py              -> core/   (replace)
  core/vege.py              -> core/   (replace)
  core/run_simulation.py    -> core/   (replace)
  core/input_summaries.py   -> core/   (replace)
  model_science.md          -> repo root (replace)

  Brown sodosol Wallumbilla 2026.soil              -> Soils/
  Wheat fallow slower roots Wallumbilla 2026.vege  -> Vegetation descriptions/   (the app reads .vege)
  Wheat fallow slower roots Wallumbilla 2026.json  -> keep for HowLeaky online; the app does not read .json

  watbal2026_oct2026.diff   -> not needed in the repo; a record of every line changed

All six core files must go in together. They depend on each other.
