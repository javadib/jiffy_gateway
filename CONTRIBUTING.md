# Contributing to Jiffy

Thanks for your interest in contributing to Jiffy! This guide covers everything from setting up the project locally to submitting your first pull request.

## Getting Started

### 1. Fork and Clone

```bash
git clone https://github.com/<your-username>/jiffy_gateway.git
cd jiffy_gateway
git remote add upstream https://github.com/javadib/jiffy_gateway.git
```

### 2. Set Up Your Environment

Jiffy Gateway is a Django + Celery + Redis application (SQLite by default, PostgreSQL-portable via config).

Requirements:
- Python 3.12
- Redis (Celery broker/backend)
- Docker (for the sandbox execution environment)

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # fill in local config (secrets, tokens, etc.)

python manage.py migrate
python manage.py runserver
```

In a separate terminal, start the Celery worker:

```bash
celery -A jiffy worker -Q execute --loglevel=info
```

### 3. Build the Sandbox Image

Task execution happens inside an isolated Docker sandbox. Build it locally before testing end-to-end flows:

```bash
docker build -t jiffy-sandbox ./sandbox
```

## Finding Something to Work On

- Check the Roadmap / project board and the `roadmap` label on Issues to see what's currently planned and prioritized.
- Issues labeled `good first issue` are a good place to start.
- Want to work on something not yet on the roadmap? Open an Issue first to discuss it — this avoids wasted work if the direction doesn't fit the project's design principles (thin Gateway, delegate to the agent, low operational overhead).

## Branching and Workflow

- All work branches off `develop`, not `main`.
- Branch naming convention: `Jiffy/<slug>` (e.g. `Jiffy/gitea-ingestion-retry`, `Jiffy/fix-callback-ttl`).

```bash
git checkout develop
git pull upstream develop
git checkout -b Jiffy/<slug>
```

- Keep commits focused and descriptive. Prefer several small, reviewable commits over one large one.
- Rebase on `upstream/develop` before opening your PR to avoid merge conflicts.

## Code Style

- **Python**: PEP 8, formatted with `black`, linted with `flake8` before committing.
- **JavaScript/Node**: follow the project's `.eslintrc` (Node 24 target). Run `npm run lint` before committing.
- **Shell/CI**: never add raw `curl`/`bash` steps to GitHub Actions where a scripted action (e.g. `actions/github-script`) can do the same job — this is a hard project convention (shell injection prevention), not a preference.
- Keep the Gateway thin: logic that can reasonably run inside the agent's sandboxed execution belongs there, not in the Gateway. If a PR adds orchestration logic to the Gateway, explain why it can't be delegated to the agent.
- No speculative abstractions or config options for hypothetical future needs — this project targets low request volume and easy self-hosting, not scale.

## Testing

- Add or update tests for any behavior change (`python manage.py test`).
- For changes touching ingestion endpoints or auth, include a test for the unauthenticated/invalid-token path — no DB or Redis writes should ever happen before auth verification.

## Submitting a Pull Request

1. Push your branch: `git push origin Jiffy/<slug>`
2. Open a PR against `develop` on `javadib/jiffy_gateway`.
3. Fill in the PR template: what changed, why, and how it was tested.
4. Link the related Issue.
5. Make sure CI passes before requesting review.
6. Be responsive to review feedback — small, iterative fixes are preferred over large rewrites.

## Questions

If anything here is unclear, open a Discussion or ask on the related Issue before starting work.
