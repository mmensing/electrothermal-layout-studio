`legacy_transient_e18.etl` comes from the native E18 acceptance run from the early transient implementation, before saved initialization,
peak retention and periodic convergence settings were added. It preserves the
original project configuration, configuration hash, physics hash, mesh, initial
and final numerical fields, and complete scalar history. Intermediate field
snapshots were removed to keep the fixture small; the retained snapshot count
was updated to two. No source configuration or identity was regenerated using
the current implementation.

Publication sanitization generalized only the machine-local layout path; the
fixture was not recalculated and retains its original physical identities.
