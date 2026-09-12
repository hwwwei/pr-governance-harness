# Security Boundaries

This repository implements a single-tenant pull request review harness.

- PR content is treated as untrusted text. The runtime never executes it and never exposes an arbitrary shell tool.
- GitHub credentials are read-only and are used only to fetch PR metadata and patches.
- Webhooks require `X-Hub-Signature-256` when a secret is configured and are deduplicated by `X-GitHub-Delivery`.
- Built-in and MCP-ready tools require an explicit role allowlist. Repository writes and undeclared tools are denied.
- Candidate patches are returned as report data. The validator parses and scopes them but does not run tests or apply changes.
- The default deployment profile does not include user authentication, tenant isolation, a code-execution sandbox, or a secret-management service. Deploy it only in a trusted internal environment.

Please report security issues privately to the repository owner rather than opening an issue with credentials or private PR content.
