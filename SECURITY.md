# Security policy

Report suspected vulnerabilities privately using GitHub's
[Report a vulnerability form](https://github.com/mmensing/electrothermal-layout-studio/security/advisories/new).
Do not disclose exploits, personal information, credentials or proprietary layouts
in public issues or pull requests. If the form is unavailable, do not post sensitive
details publicly; ask for a working private channel without describing the vulnerability.

Include the application/public source revision, platform, reproducible steps and
impact. Use a minimal synthetic project and remove secrets and identifying data.
Do not send a production PDK or customer layout. Never send a live credential;
revoke or rotate an exposed credential at its issuer.

This research prototype currently supports the current public main source. Old
pre-public binaries are not public source-edition releases. There is no guaranteed
response time or long-term support promise. Numerical model limitations are
explained in the handbook; a scientifically unexpected result is not automatically
a security vulnerability.

ETL/layout files and dependency/native parsers process structured external input.
Open only files you are authorized to use, keep the dependency lock updated through
review, and preserve archive size/path/shape validation. Public CI executes
untrusted PR tests only on isolated GitHub-hosted runners with read-only permissions
and no publishing credentials.
