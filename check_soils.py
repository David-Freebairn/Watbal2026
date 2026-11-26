"""Check soil files for consistent model configuration.

    python check_soils.py            # all soils in Soils/
    python check_soils.py path ...   # chosen files

Prints the settings that matter for every soil, then:
  1. Site soils (file name contains "_2026") that depart from SITE_STANDARD.
  2. Soils whose PAWC element disagrees with the PAWC computed from the limits.
  3. Elements in the files that the model never reads (legacy or unused).

Exits with code 1 if (1) or (2) finds anything, so it can run as a test.
Edit SITE_STANDARD when a standing decision changes.
"""
import io, re, sys, contextlib
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from core.soil_xml import read_soil_xml

# Standing decisions for the calibrated site soils (Nov 2026)
SITE_STANDARD = {
    'top_layers_mm': (100, 300),   # layer 1 0-10 cm, layer 2 10-30 cm (sampling increments)
    'U': 0.0,                      # stage 1 evaporation off; Cona carries evaporation
    'AMC': 'howleaky',             # uncapped AMC, CN entered at average wetness
    'CN_reduction': 14.8,          # CN reduction at full cover, common to all sites
    'sed_cover_coef': 0.057,       # exponential sediment-cover coefficient
}
PAWC_TOL_MM = 1.0


def describe(path):
    with contextlib.redirect_stdout(io.StringIO()):
        p = read_soil_xml(str(path))
    st = ET.parse(path).getroot().find('SoilType')
    depths = tuple(int(round(l.depth_mm)) for l in p.layers)
    pawc_calc = sum(l.pawc for l in p.layers)
    pe = st.find('PAWC')
    pawc_file = sum(float(pe.get(f'value{i + 1}', 0)) for i in range(len(p.layers))) if pe is not None else None
    return dict(
        file=path.name, layers=depths, n=len(depths), pawc=pawc_calc, pawc_file=pawc_file,
        U=p.u, Cona=p.cona, CN=getattr(p, 'cn_input', p.cn2_bare), CN_reduction=p.cn_cover_reduction,
        AMC=getattr(p, 'amc_mode', '?'),
        K=p.musle_k, sed_cover_coef=getattr(p, 'sed_cover_coef', None),
        tags=[ch.tag for ch in st])


def main(paths):
    rows = [describe(p) for p in paths]
    reader_src = (HERE / 'core' / 'soil_xml.py').read_text()
    read_tags = set(re.findall(r"['\"]([A-Za-z_0-9]+)['\"]", reader_src))

    print(f"{'Soil file':44s} {'Layers (mm)':32s} {'PAWC':>5s} {'U':>4s} {'Cona':>5s} {'CN':>5s} "
          f"{'CNred':>5s} {'AMC':>9s} {'K':>6s} {'SedB':>6s}")
    for r in rows:
        lay = '/'.join(str(d) for d in r['layers'])
        sb = '-' if r['sed_cover_coef'] is None else f"{r['sed_cover_coef']:.3f}"
        print(f"{r['file']:44s} {lay:32s} {r['pawc']:5.0f} {r['U']:4.1f} {r['Cona']:5.2f} {r['CN']:5.1f} "
              f"{r['CN_reduction']:5.1f} {r['AMC']:>9s} {r['K']:6.3f} {sb:>6s}")

    problems = []
    for r in rows:
        if '_2026' not in r['file']:
            continue
        got = {'top_layers_mm': r['layers'][:2], 'U': r['U'], 'AMC': r['AMC'],
               'CN_reduction': round(r['CN_reduction'], 3), 'sed_cover_coef': r['sed_cover_coef']}
        for k, want in SITE_STANDARD.items():
            g = got[k]
            ok = abs(g - want) < 1e-6 if isinstance(want, float) and g is not None else g == want
            if not ok:
                problems.append(f"{r['file']}: {k} = {g}, standard {want}")
    for r in rows:
        if r['pawc_file'] is not None and abs(r['pawc_file'] - r['pawc']) > PAWC_TOL_MM:
            problems.append(f"{r['file']}: PAWC element sums to {r['pawc_file']:.0f} mm, limits give {r['pawc']:.0f} mm")

    unread = {}
    for r in rows:
        for t in r['tags']:
            if t not in read_tags:
                unread.setdefault(t, []).append(r['file'])

    print('\nSite soils against the standard, and PAWC element check:')
    print('  ' + ('\n  '.join(problems) if problems else 'all consistent'))
    print('\nElements in soil files that the model does not read:')
    if unread:
        for t, fs in sorted(unread.items()):
            print(f"  {t:36s} {len(fs)} file(s)")
    else:
        print('  none')
    return 1 if problems else 0


if __name__ == '__main__':
    args = [Path(a) for a in sys.argv[1:]]
    if not args:
        args = sorted(list((HERE / 'Soils').glob('*.soil')) + list((HERE / 'Soils').glob('*.xml')))
    sys.exit(main(args))
