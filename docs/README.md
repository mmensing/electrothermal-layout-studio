# Documentation

[Project introduction](../README.md) · [Example catalog](examples.md) · [Example downloads](../examples/README.md)

The guides describe source version **0.4.2**. Check [Releases](https://github.com/mmensing/electrothermal-layout-studio/releases)
for actual Windows downloads and [release notes](releases/0.4.2.md) for this version.
About and `etlayout build-info` identify the running implementation; old result
archives keep their original identity. Published older binaries retain their own capabilities.

[Build and result identity](build-identity.md) · [Measured CPU execution](performance.md)

## Learn with the application

| Your goal | Start here |
| --- | --- |
| Complete a first GUI calculation | [Copper-strip walkthrough](handbook/first-calculation.md) |
| Choose among all forty examples | [Catalog](examples.md), [six learning paths](example-paths.md), [portable project downloads](../examples/README.md) |
| Understand the numbered workflow | [Desktop workflow](desktop-workflow.md), [user guide](user-guide.md) |
| Find a supported tool | [Feature catalog](features.md) |
| Understand assumptions and physics | [Handbook](handbook/README.md), [model and physics](handbook/model-and-physics.md) |
| Install or troubleshoot Windows | [Windows setup](windows.md), [runtime requirements](windows-runtime.md) |

## Follow an engineering question

- **Electrical paths and sensing:** [current crowding](current-crowding.md),
  [contact extraction and E27 TLM](contact-extraction.md).
- **Multilayer and power layouts:** [E20–E24 families](power-examples.md),
  [routing/optimization reference](POWER_ROUTING.md),
  [companion routing experiments](../examples/README.md#companion-routing-experiments).
- **Heating and time:** [thermal paths and heat placement](thermal-stacks.md),
  [Joule-only and thermal demonstration scenarios](thermal-example-scenarios.md),
  [pulses and thermal storage](handbook/transient.md).
- **GaN and SiC:** [recommended device scenarios](device-engineering-examples.md)
  separate voltage-biased gate control, emergent parallel-bank sharing,
  lower-current SiC operation and explicit stress cases.
- **Inspect and preserve:** [engineering results](engineering-results.md),
  [visualization workbench](visualization-workbench.md),
  [layout editing](layout-editor.md), [results and validation](handbook/results-and-validation.md).

## Assess evidence

The [example-science index](example-science.md) links exact original-reference,
thermal-scenario and device observations. Read these alongside
[scientific acceptance](scientific-acceptance.md), [local meshing](local-meshing.md)
and [temperature limits](temperature-limits.md). Convergence, conservation,
mesh/time sensitivity, engineering limits and physical calibration are distinct.
Historical observations retain their original identities.

## Develop and maintain

[Contribution guide](../CONTRIBUTING.md), [developer guide](developer-guide.md),
[architecture](architecture.md), [validation commands](validation.md),
[numerical methods](numerics.md), [geometry contract](geometry.md) and
[performance](performance.md) cover supported development workflows.

[Release procedure](maintainer-guide.md), [release notes](releases/0.4.x.md),
[licensing](licensing.md), [corresponding source](compliance-sources.md) and
[third-party notices](../THIRD_PARTY_NOTICES.md) cover publication and maintenance.
