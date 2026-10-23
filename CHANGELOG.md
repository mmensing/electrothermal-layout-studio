# Changelog

## Unreleased

- Keep port/reference names on single SPICE comment lines, including names with
  control characters; preserve exact preview/export agreement and port responses.
- Explain how to instantiate the exported resistor subcircuit in a separate
  simulator testbench, including pin order, current sign and fixed-temperature scope.
- Point the feature catalog to the dedicated passive-network workflow.

## 0.4.3 — guided physical setup and SciPy state reuse

- Guide physical stack setup, thin-film cross-sections and explicit interlayer
  electrical/thermal connections through the existing shared draft workflow.
- Add physical-port selection, reference guidance and the exact validated SPICE
  preview; retire stale networks after selection or physical input changes.
- Put result headlines first, distinguish selected-run/setup/job context, preserve
  separate setup/result cameras and keep compact drawers resizable. Add useful
  field/label hover help and display headline temperatures to 0.1 K only.
- Reuse the converged electrical state between coupled thermal iterations when
  temperature and input identity remain exact; retire it after thermal-boundary
  projection. Retain SciPy, cancellation and scientific acceptance checks.
- Bound history-reader shutdown by one deadline, consolidate immutable GUI
  fixture calculations and report measured CI test/journey durations.

See [0.4.3 notes](docs/releases/0.4.3.md) and [measured performance](docs/performance.md).
Source availability and a successful candidate build do not establish binary
publication; check [Releases](https://github.com/mmensing/electrothermal-layout-studio/releases)
for actual Windows downloads.

## 0.4.2 — Qt lifetime stability and stable CPU kernel limits

- Fix Shiboken retained-reference clearing/removal/replacement before Python
  finalization or garbage collection, using a source-built runtime with native
  regression and ABI checks. Keep normal collection and original Qt bindings.
- Apply the native one-thread kernel cap once per owned calculation, retaining
  configurable outer assembly/study workers and restoring caller limits. Preserve
  SciPy factorization and scientific tolerances; E21 measurements do not establish
  a universal speedup.
- Preserve the caller's Windows PATH/PYTHONPATH during owned Gmsh startup;
  retain the same mesh and borrowed Gmsh model ownership.
- Verify release drafts without assuming a draft has already created a Git tag;
  retain exact identity, state, notes and asset-hash gates.
- Bind modified Qt native bytes and complete preferred source in the release
  profile. Retain all 77 prior source payloads and all 42 example project bytes.

See [0.4.2 notes](docs/releases/0.4.2.md), [Qt runtime](docs/qt-runtime.md) and
[CPU measurements](docs/performance.md). Source availability and candidate checks
remain separate from owner-reviewed Windows publication.

## 0.4.1 — responsive projects and engineering workflow

- Move default eager archive/JSON opening, legacy fallback and resource-review
  retries into owned workers. Keep draft/save/history guards and exact resource
  grants; discard cancelled or obsolete generations without replacing the setup.
- Preserve the exact camera and field range when opening the issue/history drawer;
  distinguish project-open failures from computation failures.
- Display selected transient-frame time in readable engineering units in both
  the field-support caption and its tooltip.
- Record ordinary run build provenance; expose it in About, CLI, archives and
  reports. Scope result-cache reuse to that identity; retain unavailable identity
  for older saved results.
- Guide newly opened examples to Mesh & Checks and provide guarded mesh/time
  study actions beside missing result evidence. Keep compact example configuration
  controls and actions visible above tabbed inputs and model guidance.
- Generate online/offline feature tables and machine-readable capability records
  from one versioned registry, with CI staleness and route/test checks.
- Extract project-opening transition ownership from MainWindow and measure current
  E21 one/four-CPU operation. Actual assembly concurrency does not promise a total
  speedup; sparse direct factorization remains serial.

- Export source ZIPs from indexed maintained files, retaining `.gitattributes`
  and excluding untracked files and stale generated `*.egg-info` package metadata.
  Preserve standalone source builds with the same generated-metadata exclusion.

This release also carries the previously reviewed engineering changes below:

- Unify all forty example IDs under six subjects and shared GUI-led learning paths;
  expose size, routing, thermal and existing operating scenarios in a focused browser.
- Keep original quick-menu projects and scientific inputs; distinguish recommended
  scenarios, stress references, historical figures and current validation evidence.
- Make both companion routing studies discoverable, ship portable example inputs
  for installed builds, and preserve maintained guidance during gallery regeneration.

- Add independent current-crowding/Joule references, fixed-area hotspot metrics,
  physical local refinement, graded sizing and selected-metric mesh confidence.
- Add global/layer/region hard temperature limits, retained failure diagnostics,
  accepted-state transient stopping and independent engineering status.
- Support explicit thermal-only films and configured interface/device heat
  deposition while preserving default source splitting and legacy identities.
- Audit all forty exact original references; add opt-in thermal scenarios,
  GaN/SiC/package studies and finite-contact, multiple-spacing TLM extraction.
- Separate displayed transient frames from run/segment peaks; link E/K/metal-J,
  heat and temperature on exact shared support and preserve whole-run failure
  during metadata-first history inspection.
- Require scientific PR checks, release completed worker resources, record
  assembly phases and use one verified mesh in temporal refinement studies.
- Refresh GUI-led documentation, feature discovery, publication-status wording
  and setup links. Publication still requires exact native and compliance gates.

See [0.4.1 notes](docs/releases/0.4.1.md) and [engineering notes](docs/releases/0.4.x.md) for compatibility, measured
verification and remaining limitations. The original intermittent Qt/Shiboken
GC access violation remains unisolated; the worker cleanup is not a causal fix.

## 0.4.0 — extended examples and Windows distribution

- Add 16 distinct ready-to-open examples, E25–E40, spanning analytical/contact
  coupons, routed resistor/via networks, thermal/pulsed experiments, fully gated
  GaN, SiC on-state active islands and a millimeter package interposer.
- Keep E1–E24 and their stable IDs. Categorize the desktop example menu and
  separate extended nominal validation from the three-mode teaching loop.
- Provide GUI-led use cases, actual geometry/result previews, exact configured
  nominal checks, independent analytical references and portable GDSII/OASIS/ETL.
- Include opt-in content-addressed archives and metadata-first history loading
  with bounded caching, exact transient frames, complete saves and exports.
- Preserve MPL-2.0 source; use the documented GPL-3.0-only larger-work distribution
  route for Windows, with exact corresponding source, build recipes and notices.
- Build Gmsh from an audited source subset without unused restricted 3D components;
  retain all supported planar meshing and numerical workflows. Pin the public
  Python runtime/source provenance and inventory actual native libraries/fonts.
- Use Microsoft's official runtime as a separate prerequisite; restore four
  NumPy/GEOS import aliases and omit all app-local Microsoft runtime copies.
- Remove unused Mesa/legacy font metrics/demo data and unnecessary SVG authoring
  paths. Verify retained plotting/export fonts and actual desktop calculations.
- Harden bounded release/archive inspection and hash-bound private component
  review; retain manual owner publication and read-only CI.

## Public source edition — untagged

- Publish an independently reviewed source snapshot under MPL-2.0 for eligible
  original project material, preserving third-party licenses and earlier recipients' rights.
- Provide normal contribution/security guidance, owner-reviewed changes, read-only
  CI, explicit manual validation and a separate owner-only release procedure.
- Preserve the `etlayout` namespace, CLI, ETL compatibility and existing calculations.
- Include measured run-stage timings and faster exact-support reporting; numeric
  polygon/hole editing, insert/delete/move and snapping; reviewed local mesh plans
  and bounded refinement studies; clearer terminal labels and precision-sensitive scales.
- Display current density in mA/µm², sheet current in mA/µm, electric field in V/µm,
  heat density in W/mm² and thermal gradient in K/µm. Preserve SI numerical data
  and the physical limits of migrated manual ranges.

The initial public snapshot had no public release tag or binary. Historical notes below
refer to pre-public application versions, not releases in this repository. Earlier
GPL distribution rights are not revoked by this MPL source edition.

## Pre-public 0.3.1 — 2026-10-14

Example workflow/state corrections; coplanar GaN combs with insulated gate networks;
protected SiC nonactive margins; live exact thermal support; preserved physical
hashes for empty exclusions; bounded parallel FEM assembly, valid operator/factor
reuse and actual worker provenance. Revised E21/E23 references received two-level
mesh screens at unchanged 5 A/10 A service. See [engineering notes](docs/releases/0.3.1.md).

## Pre-public 0.3.0 — 2026-10-14

Seven-stage guided desktop; immutable project-owned polygon editing; five larger
parameterized power-device example families; scoped measurements, finite-feed ports,
individual via loading, passive multiports, constrained routing and equal-service
multi-scenario optimization. Hardened exact nested support, exceedance migration,
weak-link export fidelity, via rules and draft/history transitions. See
[engineering notes](docs/releases/0.3.0.md).

## Pre-public 0.2.0 — 2026-10-11

Layer/region result controls, synchronized panels, transient heat storage and pulsed
quasi-static electrical operation; saved-state initialization/continuation; mesh/time
studies, archive budgets and adjustable CPU execution. Expanded analytical gallery
and handbook. See [engineering notes](docs/releases/0.2.0.md).
