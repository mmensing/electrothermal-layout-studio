# Four fixed power cells and bounded feed routing

Open the ready-to-run [four-cell project](four_cell_feed.etl) or
[HEMT source-feed project](hemt_source_feed.etl) in the desktop application.
Each archive embeds its synthetic layout and needs the schema-2 reader in this
development version. The delivered four-cell archive opens at its existing
nominal scenario: 50 mA target-current excitation. Regenerate both from the
checkout with:

```powershell
python examples/power_routing/generate.py --output .artifacts/power-routing-example
```

Open `four_cell_feed.etl` in the desktop application. Its four fixed analytical
areal-resistor cells share frontside copper fingers, a narrow bus, a second-layer
strap, two physical vias and a finite-resistance source landing. The backside
return is held at 300 K. This is a DC electrothermal feed experiment, not a
switching transistor or breakdown model.

Three recipes bound the bus width to 3–6 µm, strap width to 4–6 µm, and via count
to 2–8 at 4 µm pitch. Fixed finger, active-cell and label masks are protected.
The unchanged narrow baseline is deliberately improvable. The experiment uses
0.05 A and 0.1 A equal-service scenarios, a 0.5 V compliance limit, scoped routing
loss/current-density objectives, temperature limits and two mesh levels for
finalists. Material values remain illustrative. An explicitly effective buffer
thermal path connects the complete frontside feed area to the backside; it is
separate from the physical copper via between frontside and upper strap.

The script also generates `hemt_source_feed.etl`. This reuses E8's existing
distributed local-gate-bias HEMT model, four fixed channel/gate regions, and its
effective calibrated-by-user thermal-stack assumptions. A localized source
landing feeds two independently bounded 6–12 µm source-bus segments. A separate
upper metal supports single/dual-ended feeds, a 6–12 µm strap and three rows of
3/5/7 physical vias with uniform, graded or staggered placement. Gate metal is
placed above the source/drain metal to remove the inherited illustrative volume
overlap before validating this physical stack. All channel, gate, field-plate,
contact and original finger masks stay fixed. Two scenarios use 1 mA/2 mA drain current,
0 V/−1 V gate bias respectively and 2 V compliance. A current-balance or thermal
improvement must be measured from the solved fields; no uniform finger current
is imposed by the generator.

The search samples six configurations from the declared discrete design space
with a fixed seed. This bounded demonstration does not exhaust all combinations
or establish a global optimum. Both examples retain all candidate failures and
mesh evidence; a candidate is publishable only after independent GDS/OASIS
round-trip calculations confirm its service and numerical improvement.

Each generated candidate is a new GDS/OASIS source file with JSON provenance;
the imported source remains immutable. Finalists must be evaluated using their
exported/reimported geometry. Geometry rules support integer-DBU width, space,
notch, via size/pitch/enclosure and transitions. A positive circular bend-radius
rule is explicitly rejected because these polygon recipes do not implement it.
