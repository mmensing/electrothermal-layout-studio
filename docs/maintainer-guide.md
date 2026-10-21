# Maintainer and publication guide

The maintainer is **@mmensing**. This guide specifies required policy and future
release steps; committed files do not enable GitHub protections. Verify actual
settings and keep sensitive audit/readback records outside the source tree.
The initial source snapshot was untagged `0.4.0.dev0`. The current source
version is `0.4.1`; check GitHub Releases for actual publication status. A version
bump or successful local candidate does not clear binary redistribution gates.

## Repository authority and bootstrap

Use an independent repository at
[mmensing/electrothermal-layout-studio](https://github.com/mmensing/electrothermal-layout-studio),
with fresh public history. Export only audited tracked content from a pinned
source snapshot. Never mirror private objects, refs, tags, notes, hooks, worktrees,
credentials or local files. Keep private/public revision mappings and rights
records outside public history. Preserve genuine contributor and third-party
attribution; ownership or Git authorship alone is not a copyright clearance.

Before content is pushed, resolve privacy/redistribution rights, retain required
notices, inspect compressed projects/layout properties and check exact source,
wheel and resource membership. Use `scripts/audit_publication.py --root PATH`
for a complete tree/archive scan and put reports outside that input tree. The
`--source` mode explicitly excludes generated caches, build outputs, environments
and artifacts; it is suitable for routine source CI, not a substitute for auditing
an actual final archive. Pattern scans do not prove rights or detect every secret.
Review images visibly and retain real numerical provenance. Revoke/rotate any
actual exposed credential at its issuer.

Create the empty destination with Actions disabled where possible. Inspect it
first; do not overwrite an unexpected existing repository. If protections need
an initialized main branch, pushing the first already-audited root commit before
applying protection is a documented one-time bootstrap exception. Apply and read
back all protections before enabling intended workflows. If setup fails, leave
Actions and publishing disabled. Do not force-push or import private history.
Future promotions are reviewed snapshots or reviewed changes, never an automatic
private-to-public mirror.

## Required server-side settings

Read back these settings through the GitHub API/UI; do not infer them from this
file or CODEOWNERS:

- `main` is the default branch. mmensing is the sole human writer/administrator.
  Contributions use forks and PRs with manual owner merging. Use squash merging;
  disable auto-merge/merge queues and delete merged feature branches as appropriate.
- Protect main with PRs, stable `source-quality` and `scientific-regression` checks, resolved conversations,
  no force pushes and no deletion. Zero approving reviews avoids impossible owner
  self-approval; owner-only write authority and manual merging remain the boundary.
- Keep quality rules separate from ref-ownership rules. Quality has no bypass.
  Restrict branch/tag creation and updates to the narrowest supported owner role.
  If only an administrator-role ownership bypass is available, verify mmensing
  alone matches it. Protect release tags against unauthorized update/deletion.
- CODEOWNERS routes review; it does not grant or restrict access. No Actions token,
  app, deploy key or integration receives a ruleset bypass. Any owner-authorized
  integration is an explicit, separately reviewed authority exception: inspect its
  current/future repository selection and permissions. Do not change unrelated
  repositories or silently authorize new integrations.
- Inspect collaborators, installed/inherited app grants, deploy keys, hooks,
  repository/environment secrets and self-hosted runners. Remove or exclude
  unauthorized write authority for this repository; record anything unverifiable
  as a blocker. Do not grant a dependency bot write access just to obtain alerts.
- Actions defaults to read-only, cannot create/approve PRs, and requires approval
  for all external contributors. Allow only the exact reviewed action revisions
  in the workflows, with `persist-credentials: false`. Use isolated hosted Windows
  runners and no publishing credentials. Enable available secret scanning, push
  protection, dependency alerts and private vulnerability reporting.
- Enable immutable releases where supported. Record plan/API limitations honestly
  and use equivalent supported controls where possible. Do not claim account
  authentication or repository settings are enabled without a readback.

Useful read-only checks include `gh api user --jq .login`, repository metadata,
`repos/OWNER/REPO/collaborators`, `/rulesets`, `/rules/branches/main`, `/keys`,
`/hooks` and `/actions/permissions/workflow`. Inspect app installation scope in
GitHub's installed-app settings when token APIs cannot establish it. Never print
credentials or copy unredacted settings dumps into public source.

## Manual owner merges

The separate ref-ownership rule can show "Cannot update this protected ref"
after every quality check has passed. The owner uses their configured
ref-ownership bypass for a manual merge. The independent main quality ruleset
has no bypass: required checks, resolved conversations and the exact tested
head must still pass. Read back both rulesets before merging; do not disable
quality rules or give an integration a bypass to clear this message.

For stacked PRs, refresh each dependent branch against current main after its
prerequisites are squash-merged. Preserve its own changes and prerequisite fixes,
inspect the refreshed diff, retarget to main and rerun both required checks on
the new exact head. An earlier green check on a different review base does not
satisfy strict up-to-date protection. Record the new head and merge receipt;
validate the resulting main commit. Never temporarily relax strict checks or use
a quality bypass to finish a batch.

## Validation and CI trust

PR updates run `source-quality`: publication/workflow contracts, redacted bounded
source inspection, lint, fast/package-policy tests, one fixed-size transient
map/state regression and CLI metadata/import.
The stable `scientific-regression` check independently compares actual Git paths
against the PR base. Physical models, solvers, geometry/meshing, persistence,
result interpretation, example layouts, tests, dependencies/configuration and
unknown paths require the real numerical regression collection before merging.
The classifier fails closed if its Git comparison is unavailable. It does not
use PR titles, labels or other untrusted prose to skip scientific work.

Only explicitly recognized prose/images/templates avoid expensive numerical PR
execution. The required check still runs and records an explicit lightweight
result; workflow-level path filters cannot leave a required check pending.
Main runs complete source/desktop validation for scientific changes; docs-only
pushes avoid those jobs. Owner-requested workflow dispatch always requests full
validation. Numerical PR runs and complete main runs are separate scopes; neither
is evidence of frozen-build or clean-machine correctness.

Activate the new required check **after its workflow is present in every open
scientific PR branch**. Read back the main quality ruleset, preserve its complete
current payload, and add GitHub Actions' `scientific-regression` context alongside
`source-quality` with the same integration identity and strict up-to-date policy.
Do not remove existing checks, conversation resolution, linear history, force-push
or deletion protections; quality retains no bypass. Confirm both contexts in the
server-side readback. Committing this guide or workflow does not enable that rule.
Merge the quality workflow first when necessary, then update dependent branches;
never temporarily weaken the rule to merge an older branch.

Expensive benchmarks and native frozen validation remain deliberate owner
requests; candidate builds are main-only. No workflow obtains a publishing token.

All workflows are read-only. They do not push, merge, tag, publish releases or
upload packages. Only clean privacy reports are uploaded; other evidence has
explicit paths and short retention. Frozen validation uploads receipts, screenshots
and manifests; publication remains an explicit local owner action. Do not promote untrusted PR
artifacts or caches into a release, even after a successful test. Source checks,
actual executable checks and clean-machine Windows testing are distinct scopes.
See [validation](validation.md) and [Windows packaging](windows.md).

## Local owner-only release procedure

The committed [binary policy](../packaging/binary-publication-policy.json) scopes
which source-built Windows profile may receive an owner release review. A local
candidate build is not clearance. The [licensing guide](licensing.md) requires
exact third-party source/build information, notices, module/native/font evidence
and the selected MPL/GPL route. A reviewed source edition remains independent.
Do not reuse or advertise pre-public binaries as MPL releases.

1. Review and merge a real version change through the public PR workflow. Use an
   unambiguous stable version for a future release, synchronized in pyproject and
   `etlayout.__version__`; retain old result solver identities. The helper rejects
   development/prerelease/local versions and existing tags/releases. It does not
   bump versions, create a commit or replace prior assets.
2. Fetch the public main, verify a clean local main at that exact protected remote
   commit, and run required source/numerical/desktop checks. For binaries, also
   rebuild and execute the actual candidate, inspect readable native screenshots
   and extracted-payload results, audit complete package contents and finish every
   license gate. Retain source/build/SBOM evidence and exact artifact SHA256 hashes.
3. Review authority again, including owner-authorized integrations and no bypass
   of quality. Authenticate using the owner's existing local GitHub CLI session.
   The helper requires login mmensing and the fixed public destination. It rejects
   CI execution and parent token environment variables; do not introduce a publishing PAT.
   `--credential-source git` may reuse the existing owner OS credential-manager
   session in a scoped GitHub CLI child when CLI login requires unrelated scopes.
   It never creates/stores a new token or requests additional account grants.
4. Place vetted assets and release notes in a local owner-controlled folder. Include
   `SHA256SUMS.json` mapping exactly each other uploaded asset filename to its hash.
   Keep an external private review manifest and hash-bound evidence outside the
   checkout. The manifest must bind the public commit/version and all assets;
   rights/privacy, notices, build information, source contents and current authority
   require explicit owner review. Binary attestations additionally require complete
   corresponding source, native/font notices and runtime prerequisite evidence.
   Set `license_route` to the committed route. The reviewed GPL/MPL section 3.3
   profile requires `gpl_source_access`; an LGPL route instead requires actual
   replacement/debugging evidence. Do not claim both by checking boxes.
   Attestations record a completed review; they do not themselves prove compliance.
5. Run the default **read-only** preflight. It checks protected remote main, no
   existing tag/release, current collaborators/deploy keys and the latest trusted
   exact-commit public-main CI run. The required `source-quality` and `scientific-regression` checks must be bound to the actual
   GitHub Actions integration, quality rules must have no bypass, and immutable
   releases must read back enabled. If immutability is unavailable, stop and review
   documented equivalent controls through a helper change. `source-quality`, `scientific-regression`, `windows-tests` and
   `windows-desktop` must all pass in an owner `push`/`workflow_dispatch` run from
   this repository, not a fork/PR run. Source-only assets receive bounded nested
   archive inspection, native-payload rejection and matching source/version/license
   checks. Source project/layout/numeric fixtures remain supported.

   ```powershell
   python scripts/owner_release.py --assets-dir ../release-assets --review-manifest ../release-review/review.json
   ```

6. Only after reviewing successful preflight, explicitly prepare a draft:

   ```powershell
   python scripts/owner_release.py --assets-dir ../release-assets --review-manifest ../release-review/review.json --prepare-draft
   ```

   The helper rechecks gates, creates a draft targeting the exact tested public
   commit and uploads only the reviewed local files without replacement. It checks
   the unique draft through the authenticated, paginated release listing and reads
   it back by release ID: an unpublished draft need not have a Git tag yet. Its tag,
   exact commit, unpublished state, title, notes, asset names, sizes and server digests
   must match the reviewed inputs. Preflight also detects existing drafts without
   tags. No automatic retry or cleanup occurs after an uncertain network result:
   inspect the existing draft rather than creating duplicates or replacing assets.
7. Inspect the unpublished draft, complete vetted source/notices/assets, checksums,
   version, exact target and immutable-release setting. With immutable releases,
   attach all final assets **before** publication: published assets/tags cannot be
   replaced. The helper has no final-publish option. When separately authorized and
   ready, the owner publishes explicitly using GitHub's UI or local CLI, for example
   `gh release edit vX.Y.Z --repo mmensing/electrothermal-layout-studio --draft=false`.
   Verify final anonymous download links/hashes afterward. No step of the initial
   source publication task executes this tagged/binary release procedure.

### External review manifest shape

This is a schema example with placeholders, not a completed review or legal clearance:

```json
{
  "schema_version": 1,
  "repository": "mmensing/electrothermal-layout-studio",
  "commit": "PUBLIC_COMMIT_40_HEX",
  "version": "X.Y.Z",
  "reviewer": "mmensing",
  "kind": "source",
  "attestations": {
    "rights_and_privacy": true,
    "license_notices": true,
    "build_information": true,
    "owner_only_authority": true,
    "source_contents_reviewed": true
  },
  "evidence": [{"name": "assessment.json", "sha256": "REVIEW_EVIDENCE_SHA256"}],
  "assets": [
    {"name": "studio-source.zip", "sha256": "SOURCE_ASSET_SHA256"},
    {"name": "SHA256SUMS.json", "sha256": "CHECKSUM_FILE_SHA256"}
  ],
  "release_notes": {"name": "release-notes.md", "sha256": "NOTES_SHA256"}
}
```

Use actual reviewed hashes and a stable public version. The helper intentionally
has bounded archive/asset limits; oversized source sets need a reviewed validation
change, not an unchecked fallback. Binary reviews also bind `license_route` and set `corresponding_source`,
`native_and_font_notices`, `runtime_prerequisites` and the selected route's
attestation true only when supported by exact evidence and a committed eligible
profile. Ordinary source checks retain their stricter source-only limits.

For a binary or third-party source asset, an optional `findings_review` references
an evidence entry's exact name/hash. The separate private finding manifest binds
the artifact and inspector code hashes, every redacted finding, its finite benign
disposition and hash-bound evidence. It cannot waive tokens, live credentials,
malformed archives, unread content or resource-limit failures. Only third-party
source reviews may classify exact published fictional authentication examples or
fixed test secrets using independently checked upstream test context; binary
candidates cannot use those dispositions. Required attribution
and public test fixtures remain intact. Changed or incomplete reviews fail closed;
private rationales and personal details never enter public release assets.

## Account-security recommendations

Use passkey/security-key-backed authentication where available, store recovery
codes safely, and review sessions, SSH keys, signing keys, tokens and installed
apps. Keep recovery details private and avoid long-lived publishing credentials.
These are recommendations; this repository cannot verify or enable account-level
controls. Record actual checks separately without exposing sensitive values.

Primary references: [GitHub CLI draft creation](https://cli.github.com/manual/gh_release_create),
[asset uploads](https://cli.github.com/manual/gh_release_upload),
[release assets/digests](https://docs.github.com/en/rest/releases/assets), and
[branch rules](https://docs.github.com/en/rest/repos/rules).
