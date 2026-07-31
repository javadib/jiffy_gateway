# Product Manager intake

Dimensions worth drawing out of a casual PM request:

- **User story** — as a [who], I want [what], so that [why]
- **Success metric** — how you'll know it worked
- **Priority/urgency** — and why, if it affects scope
- **Stakeholders affected** — other teams, customers, internal users
- **Acceptance criteria** — from a product, not implementation, point of view
- **Related existing features/flags** — anything this overlaps with or depends on

## Example

**Input:** "کاربرا میگن نمیفهمن ایمیلشون تایید شده یا نه بعد از ثبت‌نام، یه چیزی بذاریم واضح‌تر بشه"

**Plan:**
```
## Plan: Clear post-signup email verification status
**Goal:** Users should immediately understand whether their email is verified after signing up.
**Scope:**
- Add a visible status indicator (banner or badge) reflecting verification state
- Prompt to resend verification email if unverified
**Acceptance criteria:**
- Unverified users see a clear, persistent prompt until verified
- Verified users see no such prompt
**Out of scope:**
- Changing the verification email content/template itself
**Open questions:**
- Should the resend action be rate-limited, and by how much?
```
