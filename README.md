# IPS Auto Update - Air-Gapped Workflow

**Python orchestration for offline Check Point IPS updates across management servers.**

This is a sanitized portfolio adaptation of an operational script. It demonstrates SFTP transfer, management API calls, asynchronous task polling, and session cleanup. The original infrastructure identifiers and credentials are excluded.

## Operational problem

In an air-gapped environment, an approved IPS package must arrive through an authorized transfer process before an internal automation host can distribute and apply it. Repeating these steps manually across servers makes progress and failure handling harder to follow.

This tool coordinates the internal workflow. It does not download packages from the internet or bypass transfer controls.

## Safe reviewer demo

Python 3.11 or newer. No third-party dependencies, credentials, or network access are needed for dry run:

```bash
python ipsupdate.py --config config.example.json
python -m unittest discover -s tests -v
```

Expected output includes:

```text
DRY RUN lab-management-01: upload -> update -> poll -> publish -> poll -> cleanup -> logout
No network calls or package reads performed
```

## Workflow

```mermaid
flowchart LR
    A[Approved offline UPF package] --> B[Verify trusted SHA-256 digest]
    B --> C[SFTP with known-host verification]
    C --> D[API login]
    D --> E[run-ips-update and poll]
    E --> F[Publish and poll]
    F --> G[Remove staged package]
    G --> H[Logout]
```

Servers run sequentially. A failure stops the run; later servers are not attempted. Staged-package removal happens only after update and publish succeed. A failed run may leave staged files or partially completed remote work; verify remote state before retrying. Gateway policy installation is not implemented.

## Changes from the original script

| Area | Public adaptation |
| --- | --- |
| Secrets | Environment variables; no logged keys or session IDs |
| Configuration | External JSON, synthetic aliases, explicit package selection |
| HTTPS | Certificate verification enabled; optional CA bundle; redirects disabled |
| SSH | Explicit known-hosts file; unknown host keys rejected |
| Package | Required trusted SHA-256 digest; refuses overwriting a staged package |
| Tasks | Bounded polling and explicit states; publish task also polled |
| Failures | Stops after failure; attempts logout and closes connections |
| Execution | Dry run by default; remote changes require `--execute` |

## Authorized lab execution

This adaptation has offline tests only. Validate exact API behavior, status values, and permissions against your Check Point version before live use.

1. On a connected preparation machine, resolve `requirements.txt` for your internal runtime and build a reviewed locked wheelhouse. Supplied requirements are version ranges, not a reproducible offline lock.
2. Transfer approved packages and dependencies through the authorized process; install from the approved internal source.
3. Copy `config.example.json` to ignored `config.json`. Set package/key paths, server aliases, hostnames, and a trusted CA bundle when needed.
4. Populate known-hosts with independently verified SSH host keys. Provide API keys through the named environment variables using your normal secret-management process.
5. Validate dry-run configuration. Obtain a trusted package SHA-256 digest independently; integrity checking alone does not establish authenticity.
6. In an authorized lab:

```bash
python ipsupdate.py --config config.json --execute --expected-sha256 TRUSTED_64_HEX_DIGEST
```

Exit **0**: dry run completed, or all configured servers completed the workflow. Exit **1**: stopped. Logs use aliases and event status; raw API responses and exception text are omitted.

## Limitations

- No live Check Point integration test was performed for this adaptation.
- No automatic rollback, update-request retry, gateway policy installation, or package authenticity verification.
- Keep the package immutable after digest validation and during execution, using a controlled staging directory.
- An in-flight request may finish after the polling deadline; requests have separate timeouts.
- SSH/SFTP permissions and management API behavior depend on the lab.
- This is a portfolio example, not a supported production utility.

See [security notes](SECURITY.md).
