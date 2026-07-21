# Secrets Management

How to generate, store, and rotate the shared tokens that authenticate
Jiffy's edge components against the Gateway ingestion endpoints.

## Authentication mechanism

Every ingestion request must include an HTTP header:

```
X_JIFFY_TOKEN: <secret-value>
```

The Gateway verifies this header against a per-provider secret configured
via environment variables. A missing or mismatched header returns `401`
before any database or Redis access.

## Generating secrets

Use a cryptographically secure generator — never hand-type a string or
use a UUID:

```bash
# Option 1: Python
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Option 2: OpenSSL
openssl rand -hex 32
```

## Uniqueness requirements

- **One secret per provider** — `GITHUB_INGEST_TOKEN`,
  `GITLAB_INGEST_TOKEN`, and `GITEA_INGEST_TOKEN` must all differ.
- **One secret per deployment** — self-hosting teams each maintain their
  own set. Never share secrets across installations.

## Gateway-side storage (this repo)

Secrets are read from environment variables at runtime. Store them in a
`.env` file (copy from `.env.example`) with restrictive permissions:

```bash
cp .env.example .env
chmod 600 .env
# fill in real values, then verify
cat .env   # only you should be able to read this
```

`chmod 600` ensures only the file owner can read/write the file. The
`.env` file is excluded from version control via `.gitignore`.

Teams running on Kubernetes, Docker Swarm, or similar platforms may use
their platform's native secrets mechanism (Kubernetes Secrets, Docker
Swarm secrets, AWS Secrets Manager, etc.) instead of a `.env` file. This
is optional — the Gateway only needs the values available as environment
variables at process start time.

## Edge-side storage (CI/CD secrets)

The same secret value must also be stored in each provider's CI/CD
secrets store, where the edge job injects it as the `X_JIFFY_TOKEN`
header on its request to the ingestion endpoint:

| Provider   | Where to store                                                  |
|------------|-----------------------------------------------------------------|
| GitHub     | Repository or organization **Actions secrets**                  |
| GitLab     | CI/CD **masked + protected** variables                          |
| Gitea      | Repository **Actions secrets**                                  |

Example edge-job snippet (GitHub Actions):

```yaml
- name: Notify Jiffy
  run: |
    curl -X POST "$JIFFY_URL/api/github/ingestion" \
      -H "Content-Type: application/json" \
      -H "X_JIFFY_TOKEN: ${{ secrets.JIFFY_INGEST_TOKEN }}" \
      -d @payload.json
```

## Rotation

Rotation is manual for now:

1. Generate a new secret (see [Generating secrets](#generating-secrets)).
2. Update the Gateway's environment variable (`GITHUB_INGEST_TOKEN`, etc.)
   and restart the Gateway process.
3. Update the corresponding CI/CD secret in the provider's settings.
4. Trigger a new edge-job run to verify end-to-end.

A short window of mismatch (new secret on one side, old on the other)
will cause `401` responses. This is accepted at this phase — no
dual-secret (old + new simultaneously valid) support is being built yet.
If zero-downtime rotation becomes necessary later, this should be
revisited.
