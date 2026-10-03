# Security policy

## Supported versions

Security fixes are applied to the latest published `0.x` release and the
`main` branch. Older pre-1.0 releases are not maintained after a newer release
is available.

## Reporting a vulnerability

Use GitHub's **Security > Advisories > Report a vulnerability** flow for this
repository. Do not disclose suspected vulnerabilities in public issues,
discussions, pull requests, Discord messages, or logs.

Include the affected version or commit, deployment context, reproduction steps,
impact, and any suggested mitigation. Remove Discord tokens, user identifiers,
cluster credentials, and other sensitive data from evidence. Maintainers will
acknowledge a report within three business days and coordinate remediation and
disclosure with the reporter.

## Operational guidance

- Treat the Discord token as a production credential. Rotate it immediately if
  it is exposed, then replace the GitHub environment secret.
- Use a dedicated Discord bot application with no guild permissions and no
  privileged gateway intents.
- Protect the `production` environment and restrict the self-hosted runner to
  trusted repositories and maintainers.
- Keep the runner, k3s, container runtime, Actions, base image, and Python
  dependencies patched. Dependabot is configured for all three dependency
  ecosystems.
- Do not run workflows from untrusted forks on the deployment runner.
- Review changes to workflows, Kubernetes manifests, the Dockerfile, and
  generated pet data as security-sensitive changes.

The bot does not persist Discord interactions or user data. Logs contain only
startup state, aggregate command failures, and the bot's own public Discord ID;
the token and command input are never logged.

