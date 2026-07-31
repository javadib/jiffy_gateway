# Developer intake

Dimensions worth drawing out of a casual developer request:

- **What's broken or missing** — the actual feature/bug, in concrete terms
- **Expected vs. actual behavior** — especially for bugs
- **Where in the codebase** — module, service, or file if the person knows it; fine to leave open if not
- **Acceptance criteria** — what "done" looks like, ideally testable
- **Edge cases** worth calling out (concurrency, empty states, error paths)
- **Non-goals** — what NOT to touch, especially if the area is sensitive or shared

## Example

**Input:** "دکمه ثبت‌نام گاهی دوبار کلیک میشه و دو تا ریکوئست میره، باید جلوش رو بگیریم"

**Plan:**
```
## Plan: Prevent double-submit on signup button
**Goal:** Signup button should not fire duplicate requests on rapid double-click.
**Scope:**
- Disable the button (or debounce the handler) immediately on first click, until the request resolves
**Acceptance criteria:**
- Rapid double/triple clicks result in exactly one signup request
- Button re-enables on error so the user can retry
**Out of scope:**
- Backend idempotency changes (frontend fix only, unless the person says otherwise)
**Open questions:**
- Should there be a loading spinner during the request?
```
