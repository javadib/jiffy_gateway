# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Jiffy, please **do not open a public Issue**. Public issues are visible to everyone immediately and could expose the vulnerability before a fix exists.

Instead, report it privately using one of these channels:

1. **GitHub Private Vulnerability Reporting (preferred)**
   Go to the [Security tab](https://github.com/javadib/jiffy_gateway/security) of the repository and click **"Report a vulnerability"**. This opens a private advisory visible only to maintainers, where you can describe the issue and collaborate on a fix before any disclosure.

2. **Email**
   If private reporting isn't available to you, email `javadib67@gmail.com` with details. *(Replace with the project's actual security contact address.)*

### What to Include

To help us triage quickly, please include:
- A description of the vulnerability and its potential impact.
- Steps to reproduce (proof-of-concept if possible).
- The affected component (e.g. Gateway ingestion endpoint, sandbox image, CI/CD workflow, GitLab relay service).
- A suggested mitigation, if you have one.

### What to Expect

- Acknowledgment of your report within a reasonable timeframe.
- Investigation and follow-up questions if needed to assess severity.
- Coordination with you on disclosure timing once a fix is ready.
- We ask for reasonable time to release a fix before any public disclosure.

## Scope

This policy covers:
- Jiffy Gateway (Django/Celery ingestion and coordination server)
- The sandbox Docker image and its execution environment
- CI/CD integrations (GitHub Actions, GitLab CI relay, Gitea Actions)
- Authentication/token handling for per-provider ingestion endpoints

## Security Design Notes

For context, some relevant design choices already in place:
- Every ingestion request is authenticated via a per-project token before any database or cache write occurs.
- Task execution happens inside isolated, ephemeral Docker sandbox containers — never on the host running the Gateway.
- The Celery worker manages sandbox containers through a Docker Socket Proxy rather than a direct mount of the Docker socket.
- CI workflows avoid raw shell/curl steps in favor of scripted actions, to reduce injection risk.

If you believe any of these measures can be bypassed, that's exactly the kind of report we want to hear about privately.
