# Security notes

The original script is deliberately excluded. Never commit live configuration, keys, UPF packages, known-host inventories, session IDs, server logs, or raw API responses. Ignore rules are not a secret scanner.

If a real credential appeared in an earlier copy, revoke or rotate it. Sanitizing a public copy does not invalidate the original credential.

Use verified TLS certificates and SSH host keys, approved dependencies, least-privilege access, and controlled package staging. Run remote updates only after authorized lab validation.

Report problems with synthetic examples. Do not include organizational identifiers or secrets in public issues.
