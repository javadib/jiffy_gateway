---
name: jiffy-task-planner
description: Use this skill whenever someone in a software team — Developer, Tester, DevOps, Product Manager, or UI/UX Developer — describes a task casually and wants it handed off to the Jiffy automated engineering system (self-hosted AI coding agent that turns Issues into Pull Requests). Trigger on phrases like "بسپار به jiffy", "send this to jiffy", "create a jiffy task", "بذار jiffy انجامش بده", or any informal task description ("the login button double-submits", "we need a staging deploy for X", "write tests for the checkout flow") when the person's workflow involves Jiffy. This skill turns the casual request into a structured plan, gets explicit approval, then drafts an Issue (with the Jiffy mention) on GitHub, GitLab, or Gitea so Jiffy's agent picks it up. Do not call Jiffy's Gateway API directly under any circumstance — dispatch is always via Issue.
---

# Jiffy Task Planner

## What this skill does

Turns a casually-described task from any software role into a Pull-Request-ready hand-off for Jiffy, with a human approval checkpoint in the middle:

`casual task → role-aware plan → user approval → technical prompt → GitHub/GitLab/Gitea Issue (@jiffy mentioned) → Jiffy takes it from there`

## Why the shape matters

Jiffy's design keeps its central Gateway thin and deliberately blind to unvalidated requests: a lightweight component living next to the git server (GitHub Action / GitLab CI / Gitea Action) is the only thing allowed to decide "this mention is real, forward it." This skill respects that boundary — its only output is a well-formed Issue containing the `@jiffy` mention. It never talks to the Gateway directly, no matter how well you think you know its API shape. Skipping the Issue step would reintroduce the exact noise the architecture was built to keep out.

## Repository (ask once, then remember)

The Jiffy mention handle is fixed and universal: always `@jiffy`. Never ask about it or make it configurable — every Jiffy install uses the same convention.

The repository is the one thing that genuinely varies per person/team, so:
- If you don't already know the repo (from earlier in this conversation, or from memory), ask for it once — a URL is enough, e.g. `https://github.com/owner/repo`.
- Infer the provider (GitHub/GitLab/Gitea) from that URL's domain automatically — don't ask separately.
- Treat it as the sticky default from then on. Don't ask again on later tasks, in this conversation or (if your environment persists memory) in future ones either — just use it. Only re-prompt if the person explicitly says this task should go to a different repository.

## Step 1 — Identify the role

Figure out which hat the person is wearing: Developer, Tester, DevOps, Product Manager, or UI/UX Developer. Often this is obvious from phrasing ("the button misaligns on mobile" → UI/UX, "flaky test in checkout" → Tester) — infer and confirm briefly rather than always asking outright.

## Step 2 — Draw out the details

Load the reference file matching the role for the dimensions worth covering and a colloquial-to-plan example:

- `references/developer.md`
- `references/tester.md`
- `references/devops.md`
- `references/product-manager.md`
- `references/uiux.md`

Don't interrogate the person with every question on the list like a form — ask only about what's missing from what they already told you, and make reasonable inferences for the rest. State any assumption you made so they can correct it.

**Use an interactive choice tool for this, not plain text, whenever the question has a small set of likely answers** — for example `ask_user_input_v0` in Claude's consumer apps, which shows tappable options instead of requiring the person to type. This applies to role selection, any multiple-choice detail from the reference files, and anything else with clean options. Reserve plain text for things that can't be reduced to options — the free-form task description itself, the repo URL, or a person's reasons for rejecting a plan. If no such interactive tool exists in your environment, fall back to plain text with clearly enumerated options.

## Step 3 — Plan it, then get approval through a pop-up

Present the plan **concisely**: a one-line summary plus a short bulleted list of the actual task titles/scope items — not long prose. For example:

```
خلاصه: جلوگیری از دوبار-کلیک روی دکمه ثبت‌نام
کارها:
- غیرفعال‌سازی دکمه بلافاصله بعد از کلیک اول
- فعال‌سازی مجدد در صورت خطا
خارج از scope: تغییرات idempotency سمت بک‌اند
```

Immediately follow this with an interactive pop-up asking for approval — options like "تایید می‌کنم" and "نیاز به تغییر دارم". This is a hard checkpoint: don't draft the Issue until you get a clear approval.

- If they pick "نیاز به تغییر دارم" (or equivalent), ask in plain text what should change — this needs their own words, not a tappable option — then revise the plan and show the pop-up again.
- If the task was already clear (no clarifying questions were needed in Step 2) and the very first pop-up comes back approved, move straight into Step 4 and 5 without adding extra confirmation steps in between.

## Step 4 — Compose the technical prompt

Once approved, translate the plan into a precise prompt for Jiffy's coding agent. Write this part in English regardless of the conversation's language, since that's what the agent expects. Structure:

```
## Task
## Context
## Acceptance Criteria
## Constraints / Non-goals
## Suggested area(s) of the codebase (if known)
```

This isn't shown back for a second approval — Step 3 was the approval gate. Keep it tight; don't restate project background the agent doesn't need.

## Step 5 — Dispatch via Issue

Build the Issue:
- **Title**: short, matches the plan's title
- **Body**: opens with `@jiffy`, followed by the Step 4 prompt

Create it using whichever Git-provider tool is available in this environment for the repo's provider. If none is connected, don't attempt a raw API call or guess credentials — hand the person the finished title + body to paste themselves, and mention they could connect that provider's tool for direct posting next time.

## Step 6 — Confirm and hand off

Share the Issue link (or the drafted content, if posted manually) and tell the person Jiffy will pick it up once its bot notices the `@jiffy` mention, then report back on the same Issue with a PR link once done.
