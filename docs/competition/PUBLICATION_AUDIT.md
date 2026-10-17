# Public repository publication audit

Status: in progress

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
