# Tester intake

Dimensions worth drawing out of a casual QA/testing request:

- **What flow or feature** needs coverage
- **Test type** — unit, integration, end-to-end
- **Existing suite/framework** — where current tests live, so new ones match convention
- **Coverage target** — a specific gap, or general hardening
- **New tests vs. fixing flaky ones** — different task shape
- **Pass/fail criteria** — what counts as the task being done

## Example

**Input:** "تست‌های checkout هی فلیکی میشن روی CI، یه نگاه بنداز درستشون کن"

**Plan:**
```
## Plan: Fix flaky checkout test suite
**Goal:** Stabilize the checkout test suite so it passes reliably on CI.
**Scope:**
- Investigate and fix flaky tests under the checkout suite (timing issues, shared state, unmocked network calls, etc.)
**Acceptance criteria:**
- Suite passes consistently across multiple CI runs
- Root cause of flakiness documented per fixed test, not just retried into passing
**Out of scope:**
- Adding new test coverage (unless flakiness reveals a real, unrelated bug worth flagging)
**Open questions:**
- Any specific test names already known to be flaky, or should the agent survey the whole suite?
```
