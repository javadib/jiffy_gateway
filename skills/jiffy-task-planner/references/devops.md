# DevOps intake

Dimensions worth drawing out of a casual infra/ops request:

- **Target environment** — staging, prod, local dev
- **Infra component** — CI pipeline, Docker image, container orchestration, IaC (Terraform, etc.)
- **The actual change** requested
- **Rollback plan** — how to undo it if it goes wrong
- **Secrets/config handling** — anything sensitive that shouldn't end up in a diff or log
- **Monitoring/alerting** impact — does anything need a new alert, or could an existing one fire

## Example

**Input:** "میخوایم یه استیجینگ جدا برای تست PR ها داشته باشیم، الان همه رو رو یه محیط تست میکنیم و قاطی میشه"

**Plan:**
```
## Plan: Isolated per-PR staging environment
**Goal:** Each open PR gets its own ephemeral staging environment instead of sharing one.
**Scope:**
- Provision a staging instance per PR (naming convention, teardown on PR close/merge)
**Acceptance criteria:**
- Opening a PR spins up an isolated environment reachable via a predictable URL/subdomain
- Environment is torn down automatically when the PR closes or merges
**Out of scope:**
- Production infra changes
**Open questions:**
- Expected concurrent PR volume, to size resource limits appropriately
- Any secrets that need per-environment values vs. shared ones
```
