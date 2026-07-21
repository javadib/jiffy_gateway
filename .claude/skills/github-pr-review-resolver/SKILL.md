---
name: github-pr-review-resolver
description: Reads the review comments/threads on a GitHub Pull Request (from human reviewers or bots like gemini-code-assist, coderabbit, etc.), decides for each one whether to resolve it (apply the suggested change) or reject it (with a logical, stated reason), applies the accepted changes to the code and pushes them, then posts a final summary report as a PR comment. Use this skill whenever the user gives a GitHub PR URL and asks to read/address/resolve/reject code review comments or feedback, apply reviewer suggestions, or reply to a PR review — even if they just say "handle the reviews on this PR" or paste a PR link with review-related instructions.
---

# GitHub PR Review Resolver

Applies or rejects the review feedback on a given GitHub Pull Request, then posts a summary report back to the PR as a comment.

## Required Inputs — Extract or Ask

Before starting, make sure you have these from the user's request. **Do not assume or hardcode any of them** — they vary per invocation:

1. **PR URL** (e.g. `https://github.com/<org>/<repo>/pull/<number>`) — required.
2. **Report language** — e.g. English, Persian, or bilingual. If the user doesn't specify this explicitly, default to the language of the user's own request (write the report in Persian if they wrote in Persian, in English if they wrote in English). Only ask if the request is genuinely mixed-language or the user asks for something unusual (e.g. a third language, or explicitly bilingual).
3. **Whether to mention a bot/user at the end of the report** (e.g. `@gemini-code-assist`) — only mention one if the user explicitly asked for it. Never add a mention on your own initiative.
4. **Scope, if any** — sometimes the user restricts this to a specific reviewer, file, or subset of comments (e.g. "only address gemini-code-assist's comments, ignore the rest"). Check for this before fetching.

If the bot mention isn't stated, don't add one — never guess a mention. Language defaults to the request's own language (see above) without needing to ask; only ask a clarifying question if something else is genuinely unclear (e.g. the PR URL, or scope of which comments to address).

## Prerequisites

- `gh` CLI must be authenticated (`gh auth status`) with access to the target repo.
- The repo must already be cloned locally, or clone it (`gh repo clone <org>/<repo>`) and `cd` into it before making changes.
- Check out the PR's branch locally: `gh pr checkout <number>`.

## Workflow

### 1. Fetch the review data

Pull both the structured review threads (inline code comments, which carry resolve/unresolve state) and the general PR conversation:

```bash
# Overall PR info, reviews, and top-level comments
gh pr view <number> --repo <org>/<repo> --json title,body,comments,reviews

# Inline review comments (file, line, body, in_reply_to, etc.)
gh api repos/<org>/<repo>/pulls/<number>/comments
```

If you need the GraphQL review-thread IDs (needed later to mark threads as resolved via the "Resolve conversation" mechanism, not just posting a reply), fetch them with:

```bash
gh api graphql -f query='
  query($owner:String!, $name:String!, $number:Int!) {
    repository(owner:$owner, name:$name) {
      pullRequest(number:$number) {
        reviewThreads(first:100) {
          nodes { id isResolved comments(first:10) { nodes { id body path line author { login } } } }
        }
      }
    }
  }' -f owner=<org> -f name=<repo> -F number=<number>
```

### 2. Triage each comment/thread

For every distinct review comment or thread, decide **resolve** or **reject**, and write down the reason as you go (you'll need this for the final report). Base the decision on:

- Whether the suggestion is technically correct and improves the code (correctness, security, performance, readability, project conventions).
- Whether it's a false positive, already handled elsewhere, out of scope, or based on a misunderstanding of the code — these get rejected, with a specific reason referencing the actual code.
- Whether it conflicts with an explicit project convention (check `CLAUDE.md` / project instructions if present) — if a reviewer's suggestion contradicts a documented project decision, reject it and cite the convention.
- If genuinely ambiguous or high-impact (e.g. a suggested architectural change, not a local fix), don't silently pick a side — flag it for the user in the final report instead of guessing.

Keep a running list, e.g.:

```
1. [file.py:42] "Use context manager for file handling" — RESOLVED: applied, safer cleanup on exceptions.
2. [utils.py:10] "This function is unused, remove it" — REJECTED: used via reflection in tests/test_dynamic.py.
3. [models.py:88] "Add index on this column" — RESOLVED: applied, matches existing indexing pattern.
```

### 3. Apply the resolved changes

- Make the code edits for every item marked RESOLVED.
- Keep edits scoped to what the comment asked for — don't bundle in unrelated refactors.
- Commit with a clear message referencing the PR review, e.g. `git commit -m "Address review feedback on PR #<number>"`.
- Push to the PR's branch: `git push origin HEAD`.

### 4. Mark threads as resolved on GitHub (if the repo uses inline review threads)

For each accepted inline thread, resolve it via GraphQL using the thread ID collected in step 1:

```bash
gh api graphql -f query='
  mutation($threadId:ID!) {
    resolveReviewThread(input:{threadId:$threadId}) { thread { isResolved } }
  }' -f threadId=<thread_id>
```

Only do this for threads you actually addressed or explicitly rejected with a reason — don't resolve threads you skipped.

### 5. Post the final report as a PR comment

Compose a single comment summarizing every item and its outcome, in the language the user specified. Structure:

- One line per comment/thread: what it said, whether resolved or rejected, and the reason.
- If anything was flagged as ambiguous/high-impact and deliberately left for human judgment, call that out clearly at the top.
- End with the requested mention (e.g. `@gemini-code-assist`) **only if the user asked for one** — omit this line entirely otherwise.

Post it with:

```bash
gh pr comment <number> --repo <org>/<repo> --body "<report text>"
```

### Report template (adapt language per the user's request)

```
## Code Review Resolution Summary

| # | Location | Comment | Decision | Reason |
|---|----------|---------|----------|--------|
| 1 | file.py:42 | Use context manager | ✅ Resolved | Applied — safer cleanup on exceptions |
| 2 | utils.py:10 | Remove unused function | ❌ Rejected | Used via reflection in tests/test_dynamic.py |

<mention line, only if requested>
```

For a Persian or bilingual report, translate the structure and wording naturally — don't just machine-translate labels; write it the way a Persian-speaking engineer would phrase a PR comment.

## Notes

- If the PR has no review comments yet (e.g. bot review hasn't run), say so and ask whether to wait/retry or proceed with just human comments.
- If `gh` isn't authenticated or lacks repo access, report that clearly instead of guessing at credentials.
- Never fabricate a resolution reason — every REJECTED item must cite something concrete (a line of code, a test, a project convention doc), and every RESOLVED item must correspond to an actual code change you made.
