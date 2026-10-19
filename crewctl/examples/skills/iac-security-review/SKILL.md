---
name: iac-security-review
description: Use when reviewing Terraform, Kubernetes, Helm, Dockerfile or CI pipeline changes for security problems. Produces a findings list with file:line evidence and a severity per finding. Read-only.
triggers: review terraform, review iac, k8s manifest review, dockerfile security, pipeline security review, helm chart review
---

# IaC security review

Read the change and report security problems with evidence. Never apply, plan or deploy anything.

## Steps

1. List the changed files (`git diff --name-only <base>...HEAD`) and group them: Terraform, Kubernetes/Helm, Dockerfile, CI.
2. Read each changed file in full, then the files it references (modules, values, base images).
3. Check by type:
   - **Terraform:** public ingress (0.0.0.0/0 on non-HTTP ports), `*` in IAM actions or resources, unencrypted storage or databases, public buckets, secrets in variables or defaults, state backend without locking or encryption, missing logging.
   - **Kubernetes/Helm:** privileged or root containers, hostPath/hostNetwork, missing resource limits, `latest` tags, secrets in env or ConfigMaps, wildcard RBAC, no NetworkPolicy.
   - **Dockerfile:** unpinned base image, running as root, secrets in `ARG`/`ENV`/layers, `curl | sh`, broad `COPY .`.
   - **CI:** untrusted input in shell steps, long-lived credentials instead of OIDC, third-party actions not pinned to a commit, write permissions on pull-request triggers.
4. If `tfsec`, `checkov`, `trivy` or `kube-linter` are installed, run them read-only and merge their results. If not, say so; do not install anything.
5. Only report what you read. Mark anything inferred as "unverified".

## Output

One table: `severity | file:line | problem | evidence (quoted line) | fix`. Severities: critical, high, medium, low. End with "Not checked:" listing what you could not see (modules not in the repo, remote state, runtime config).

## Gotchas

- A default value in a variable file is still a finding if it opens access.
- A finding in code that is removed by the change is not a finding.
