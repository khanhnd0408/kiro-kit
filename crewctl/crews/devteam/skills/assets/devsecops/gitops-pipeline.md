# GitOps and pipeline design

## GitOps principles
1. The desired state is declarative. 2. It is stored in Git, versioned and immutable. 3. An agent in the cluster pulls approved changes automatically (Argo CD or Flux). 4. The agent continuously reconciles actual state to the desired state and reports drift.
Consequences: no one runs `kubectl apply` by hand against production; changes go through pull requests; rollback is `git revert`; the cluster credentials never leave the cluster.

## Repository layout
- **Application repo:** code, tests, Dockerfile. CI builds, scans, signs and pushes an image, then opens a pull request to the config repo with the new image digest.
- **Config repo (or folder):** Kustomize overlays or Helm values per environment (`base/`, `overlays/dev|staging|prod`). Promotion = a pull request moving the digest from one environment to the next. Production needs review and a required status check.
- **Infrastructure repo:** Terraform/OpenTofu modules with pinned versions, remote state with locking and encryption, `plan` posted on the pull request, `apply` only from the main branch by the pipeline.

## Pipeline stages (each one blocks the next)
1. Lint and format. 2. Unit tests. 3. Secret scan (gitleaks or trufflehog). 4. Static analysis (SAST) and dependency review. 5. Build with a pinned, minimal base image. 6. Generate an SBOM (CycloneDX or SPDX). 7. Scan image and dependencies (see `cve-handling.md`). 8. Policy checks on IaC and manifests (conftest/OPA, Kyverno CLI, checkov). 9. Sign the image and attach provenance (cosign, SLSA). 10. Push by digest. 11. Update the config repo. 12. The GitOps agent deploys; progressive rollout (Argo Rollouts or Flagger) with automatic rollback on SLO breach.

## Pipeline security rules
- CI uses short-lived credentials from OIDC federation to the cloud; no long-lived keys in variables. Least privilege per job; separate build and deploy permissions.
- Pin third-party actions/orbs/templates by commit SHA. Do not run untrusted code with secrets (`pull_request_target` and fork builds need care). Ephemeral runners.
- Protect main: required reviews, required checks, signed commits or tags where policy needs it, no force-push.
- Secrets in Git only encrypted (SOPS with age/KMS, Sealed Secrets) or referenced (External Secrets with Vault or a cloud secret manager).
- Admission control in the cluster: verify signatures, forbid privileged pods, `latest` tags and unknown registries (Kyverno or Gatekeeper). Default NetworkPolicy deny.

## Monitoring and operations
SLOs per service; alert on symptoms (error rate, latency, saturation, burn rate), not on every cause; every alert links a runbook; dashboards as code; log, metric and trace correlation by trace id; deploy markers on dashboards. Track DORA metrics (deployment frequency, lead time, change failure rate, time to restore). Backups tested by restoring. Cost and capacity reviews.

## Design output
State the stages with the tool chosen for each and why, the trust boundaries, where credentials live, how a bad release is stopped and reverted, and how long a vulnerability fix takes from advisory to production.
