# Build and result identity

**Help → About** and `etlayout build-info` identify the running application.
The machine-readable command returns the product version, exact Python
implementation SHA-256, source/installed/frozen edition, Python/platform and
runtime distribution versions. It omits installation paths, usernames and hostnames.
`etlayout --version` remains a single version; `etlayout capabilities` retains its
CPU/thread-control contract.

A source or installed-wheel fingerprint hashes the exact shipped Python files,
including relative names. Frozen packages use the source digest stamped before
freezing; the current application version must agree with a versioned stamp.
Missing or malformed frozen stamps fail explicitly. The stamp identifies Python
implementation inputs, not native DLL equivalence or successful validation.
The Windows build/SBOM manifests separately identify actual shipped native files.

Ordinary calculations record this receipt at execution start in `execution.build`.
Steady and transient results, schema-2/schema-3 archives, numerical exports and HTML
reports retain that receipt. Passive multiport extraction records its own current
build. Optimization uses the same canonical implementation digest for resumable
experiment identity. Cached numerical results are scoped to the build receipt as
well as physical configuration and execution settings, preventing reuse under a
changed implementation.

Saved results without this receipt retain their original `solver_version` and say
that implementation provenance is **unavailable**. Opening or exporting an old
result does not attach today's identity. Rerunning establishes a new receipt.
Changing build identity does not change `physics_hash` or `configuration_hash`;
execution preferences and software provenance remain outside the physical model.
Explicit figure/data sharing includes only an allowlisted version/digest identity,
not arbitrary saved execution metadata.

The source version can be newer than the latest downloadable executable. Consult
[GitHub Releases](https://github.com/mmensing/electrothermal-layout-studio/releases)
for actually published builds. A version or fingerprint does not establish
convergence, mesh verification, calibration or redistribution clearance. Follow the
[maintainer release procedure](maintainer-guide.md) for candidate and publication gates.
