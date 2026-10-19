# Contributing

Contributions arrive through forks and pull requests. The maintainer, **@mmensing**,
reviews and merges changes manually. Public access does not grant upstream write
or release authority. Keep each pull request focused, explain the observable
behavior and include appropriate validation.

## Contribution terms

By submitting original changes for inclusion, you agree to distribute them under
MPL-2.0. Submit only work you are authorized to contribute, including any employer
or institutional rights. Identify copied or adapted material, its source and its
license; preserve copyright notices, contributor credit and required attribution.
Do not upload proprietary layouts, PDKs, customer data, credentials or restricted
material databases. A contribution is not a copyright assignment or permission
to relicense unrelated third-party code. Discuss incompatible material before submission.

## Development

Follow the Windows/Python 3.12 installation in the [README](https://github.com/mmensing/electrothermal-layout-studio/blob/main/README.md#start-from-source-on-windows).
The exact wheel lock includes test/build tools. Use a feature branch in your fork.

```powershell
python -m pytest -m 'fast or packaging' --basetemp=.pytest_fast
python -m ruff check .
python -m pytest --basetemp=.pytest_full
```

The full suite includes numerical and desktop integration and takes longer.
PR CI requires source-quality contracts and a stable scientific-regression check.
Scientific changes run real numerical tests; recognized documentation-only changes
receive a lightweight scope check. Unknown paths require numerical validation.
Main scientific changes and owner-requested validation run the complete source
and desktop suite. Frozen builds and large benchmarks are deliberate maintainer
tasks, not required for every edit.
See [validation](https://github.com/mmensing/electrothermal-layout-studio/blob/main/docs/validation.md) for commands and evidence scopes.

Use SI internally and label units at display/export boundaries. Backend modules
must remain independent of Qt. Preserve component-local fields, exact support,
conservation, result identity, project compatibility and cancellation. Never
renormalize currents/losses or loosen tolerances merely to pass a test. Numerical
changes need independent analytical or conservation evidence; geometry changes
need hierarchy/transforms/holes; workflow changes need observable draft/history,
worker, persistence and error-path tests. Preserve failed study rows and distinguish
coarse feasibility from mesh verification.

Keep execution preferences separate from project physics. CPU budgets are shared
across assembly, native libraries and study processes. Widget updates remain on
the Qt main thread; calculations and IPC deserialization stay off it. See
[architecture](https://github.com/mmensing/electrothermal-layout-studio/blob/main/docs/architecture.md) and [developer guidance](https://github.com/mmensing/electrothermal-layout-studio/blob/main/docs/developer-guide.md).

Update maintained user/developer documentation and offline help when behavior
changes. Retain useful scientific provenance, but keep bulky logs, repeated
captures and generated fields in short-lived CI artifacts. Do not add development
conversations, task prompts, tool-specific handoffs or private repository links.
Use synthetic, redistributable fixtures. Report vulnerabilities through
[SECURITY.md](https://github.com/mmensing/electrothermal-layout-studio/blob/main/SECURITY.md), not public issues.
