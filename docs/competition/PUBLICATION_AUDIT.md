# Public repository publication audit

Status: local candidate scanned; remote publication pending authenticated GitHub access

## Export boundary

- New repository and new Git history; the private source repository history is excluded.
- Source was exported from the pinned competition baseline using an explicit path allowlist.
- The uncommitted competition development plan was copied separately because it was not part of
  the pinned Git tree.
- The obsolete private-repository runbook was deliberately excluded from the public candidate.

## Excluded material

- `.git/`, `.env`, `.env.*` except empty examples
- `data/private/`, databases, logs, authentication caches
- cloud credentials, service-account files, private certificates and keys
- virtual environments, dependency directories and build artifacts

## Checks required before every public push

1. Review every versioned filename and the complete staged diff.
2. Search versioned content for credentials, private keys, personal email addresses and private
   repository URLs.
3. Confirm example configuration contains blank or unmistakably synthetic values only.
4. Confirm frontend bundles contain no shared server token or private configuration.
5. Confirm the commit author uses this repository's non-personal project identity.
6. Confirm the remote is the new public competition repository, never the private source remote.

The GitHub account and public repository URL identify the account owner on GitHub. Removing names
from files cannot make that platform-level identity anonymous.

## 2026-10-17 local results

- Repository history starts with a new root commit; the private source remote and history are not
  present.
- Local commit identity is `GrowthOS Builder Cup <growthos-builder-cup@users.noreply.github.com>`.
- Baseline and competition tests pass; static frontend export and the local container health check
  pass.
- Production frontend dependency audit reports zero vulnerabilities at high/critical threshold.
- No shared server token is used by the frontend. Firebase ID tokens are obtained per anonymous
  session and sent dynamically.
- The complete staged candidate and generated frontend bundle were scanned after documentation
  changes. No common credential pattern, personal email, original private-repository identifier,
  shared frontend token or loopback API address was found. Empty `.env.example` keys and one
  synthetic unit-test secret remain intentionally. Repeat this scan immediately before any later
  public push if the candidate changes.
