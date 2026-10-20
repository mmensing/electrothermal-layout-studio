# Electrothermal Layout Studio handbook

Use a layout to ask three practical questions: **where does current flow, where is
heat generated, and how does the stack remove that heat?** This handbook explains
the model, the calculation workflow, and how to decide whether a result answers
your question.

These pages live in the repository so documentation, examples and implementation
can be reviewed together in GitHub pull requests. They render directly on GitHub,
work in a local Markdown reader, and require no separate wiki service.
The instructions cover the matching source application, retaining supported
project and CLI compatibility. Install from the
[repository README](../../README.md#run-from-source). The published
[0.4.0 Windows release](https://github.com/mmensing/electrothermal-layout-studio/releases/tag/v0.4.0)
remains available; newer source-only engineering features do not update that
binary. Read the [unreleased notes](../releases/0.4.x.md) for those additions.
Historical observations retain their original source identities; only matching
checks validate the configuration being inspected.

The [power-routing guide](../POWER_ROUTING.md)
covers scoped measurements, physical via/port loading, deterministic routing recipes,
and multi-scenario optimization. The [scientific acceptance contracts](../scientific-acceptance.md)
define exact support, acceptance constraints, circuit exports and fabrication checks.

The [power-device examples](../power-examples.md) add long-finger and
multi-bank GaN, millimeter RDL and SiC MOSFET on-state interconnect models. They
preserve the small analytical references and distinguish generated inputs,
converged calculations, mesh sensitivity and resource-limited benchmarks.

## Choose a starting point

| Your goal | Start here |
| --- | --- |
| Explore the desktop capabilities | [Feature catalog](../features.md) |
| Choose a GUI use case | [All-forty catalog](../examples.md), [six learning paths](../example-paths.md) |
| Run a first calculation | [First calculation: a copper strip](first-calculation.md) |
| Understand what is being solved | [Model and physics](model-and-physics.md) |
| Compare passivation, dielectric heat paths and source depth | [Thermal-stack setup and verification](../thermal-stacks.md) |
| Build a model from your own GDS/OASIS | [Geometry, materials and boundary conditions](setup-and-boundaries.md) |
| Move layout vertices while preserving an import | [Project-owned layout editor](../layout-editor.md) |
| Choose electrical, thermal or coupled operation | [Calculation workflows](workflows.md) |
| Model pulses, startup and cooldown | [Transient and pulsed operation](transient.md) |
| Follow multilayer examples | [Worked examples: E1, E10 and E12](worked-examples.md) |
| Read maps, compare runs and assess accuracy | [Results, exports and validation](results-and-validation.md) |
| Use Python/CLI or diagnose a problem | [Automation and troubleshooting](automation-and-troubleshooting.md) |
| Adjust CPU assembly threads or parallelize a study | [CPU execution and Python studies](automation-and-troubleshooting.md#cpu-execution-and-python-studies) |
| Extend an example experiment | [Operating scenarios](../thermal-example-scenarios.md), [device studies](../device-engineering-examples.md), [downloadable projects](../../examples/README.md) |
| Assess power feeds or optimize bounded routes | [Power interconnects and equal-service routing](../POWER_ROUTING.md) |

Read the first calculation, model and worked-example pages in order for a guided
introduction. Use the remaining pages as task references.

## The calculation in six steps

1. **Define the domain.** Select a layout cell or instance and decide which part
   belongs in the simulation.
2. **Assign physical meaning.** Map masks to physical sheets; set thickness,
   material and elevation; declare contacts and heat-transfer paths.
3. **Apply the experiment.** Add electrical terminals, heat sources and thermal
   reservoirs. A layout alone does not specify a physical problem.
4. **Validate and mesh.** Resolve connectivity and boundary warnings before
   refining the triangulation.
5. **Solve and inspect.** Read convergence and balance diagnostics, then explore
   the appropriate layer, field and region.
6. **Check and preserve.** Refine relevant quantities, compare with a known limit
   or measurement, and save the project plus result provenance.

The desktop and headless API use the same solver. Desktop result-layer choices,
color scales and region filters affect presentation; physical geometry, materials
and boundary conditions define the calculation.

For time-dependent heating, follow the [transient chapter](transient.md). It adds
thermal storage and configurable drive waveforms while retaining quasi-static
electrical states. E17 provides an analytical thermal RC comparison; E18 adds
temperature-dependent electrical pulse heating. E19 demonstrates a delayed passive
receiver response through a shared thermal substrate. The chapter also covers
saved initial states, continuation, periodic detection and time-step studies.

## What the model represents

Each physical layer is a thickness-averaged sheet with its own potential and/or
temperature. A conforming planar mesh resolves lateral detail. Explicit interfaces
exchange current or heat vertically. This is a **2.5D conduction model**: adding a
layer's elevation does not introduce full three-dimensional field resolution.

The semiconductor and HEMT options are effective resistive transport models.
They do not solve carrier continuity, breakdown, trapping, or detailed gate
electrostatics. Material seeds and example operating points illustrate the
workflow; they are not process calibration or device operating limits.

## Terms used throughout

| Term | Meaning in this application |
| --- | --- |
| Mask | A GDS/OASIS layer/datatype geometry selection, such as `1/0` |
| Physical layer | A sheet with a material, thickness, elevation and role |
| Interface | An explicit connection between two sheets over a selected overlap |
| Region | A geometry selection for an input, or a filtered subset of a result |
| Mesh | The planar triangle arrangement shared by the sheet discretizations |
| Run | A solved configuration with fields, diagnostics, metrics and identity hashes |
| Coupled | Electrical loss and temperature are updated until self-consistent |
| Converged | The configured numerical stopping and balance checks passed |
| Validated for use | The additional judgment that assumptions, inputs and discretization suit the intended experiment |

## Reference and evidence

The detailed [physics reference](../physics.md), [numerical method](../numerics.md),
[geometry contract](../geometry.md) and [architecture](../architecture.md) explain
implementation details. The [guided desktop workflow](../desktop-workflow.md) contains current command
and inspector instructions; [Windows setup](../windows.md) covers installation and packaging.

The walkthrough values are recomputed by
[`verify_walkthroughs.py`](verify_walkthroughs.py) from the checked-in example
projects. Their [machine-readable receipt](evidence/walkthroughs.json) includes
configuration hashes, mesh sizes, diagnostics and unrounded results. Receipts
retain the solver version that produced them; they are historical evidence, not
a claim that they were regenerated for every documentation release. Current capability/check scope is summarized in
[implementation status](../implementation-status.md); [validation](../validation.md)
separates current checks from historical observations.

Original project source uses [MPL-2.0](../../LICENSE). Dependencies retain their
own licenses and exceptions; read [licensing](../licensing.md) and
[third-party notices](../../THIRD_PARTY_NOTICES.md) before redistribution.
The [maintainer guide](../maintainer-guide.md) governs owner-reviewed publication;
published binaries and newer source-only capabilities are identified separately.

**Next:** [Run the first calculation →](first-calculation.md)
