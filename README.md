# CodeScribe

CodeScribe is a local-first AI documentation, PR intelligence, and AI review platform for
GitHub pull requests. It receives or extracts PR diffs, analyzes changed code with AST and
pattern parsers, generates documentation and review artifacts, scores risk and quality, stores
human feedback, and can optionally publish review comments back to GitHub.

The default LLM provider is Ollama, so CodeScribe can run without paid API keys. When Ollama is not
available, the application falls back to deterministic local output so review workflows still
complete.

## Project Overview

CodeScribe turns pull request activity into review-ready intelligence:

- Documentation drafts for changed functions, classes, modules, PRs, and release notes.
- PR intelligence reports covering classification, risk, security, quality, impact, and dependency
  signals.
- AI review decisions and inline review comments that can be held for human approval.
- Feedback and evaluation data for measuring review quality over time.
- Two execution modes: a long-running FastAPI webhook service and a GitHub Actions/CLI mode.

## Problem Statement

Engineering teams often lose context in pull requests. Reviewers need to understand what changed,
which code paths are affected, whether the PR introduces security or operational risk, and whether
documentation and tests are sufficient. This context is usually rebuilt manually in every review.

CodeScribe automates that first pass while keeping humans in control. It reads real PR diffs,
extracts code structure, generates grounded documentation, identifies risky patterns, and produces
reports and review comments that reviewers can approve, modify, reject, or publish.

## Key Features

- GitHub webhook ingestion for pull request events.
- GitHub Actions mode through `codescribe analyze-pr` and the reusable `action.yml`.
- Python AST parsing plus lightweight Go, Java, and TypeScript symbol detection.
- Ollama-backed structured JSON generation with optional Gemini support.
- Deterministic fallback provider when local LLM services are unavailable.
- PR classification for feature, bug fix, refactor, documentation, security, infrastructure,
  dependency, and test changes.
- Risk scoring from changed files, lines changed, critical paths, database changes, security
  signals, and infrastructure changes.
- Security detection for hardcoded credentials, SQL injection patterns, dangerous permissions, and
  unsafe shell commands.
- Review decisions: `APPROVE`, `REQUEST_CHANGES`, or `NEEDS_HUMAN_REVIEW`.
- Human approval endpoints before review publishing.
- Feedback metrics and JSONL dataset export for future evaluation or fine-tuning.
- Docker Compose stack for FastAPI, PostgreSQL, Redis, and Ollama.

## Architecture Overview

CodeScribe has two operating modes:

- **Webhook Server Mode**: GitHub sends PR webhook events to the FastAPI service at
  `/api/v1/webhooks/github`. CodeScribe fetches changed files and diffs through the GitHub API,
  processes the PR, persists metadata, and prepares review artifacts.
- **GitHub Actions Mode**: A pull request workflow runs `codescribe analyze-pr` directly inside
  GitHub Actions. This mode does not require a deployed webhook server and writes Markdown reports
  as workflow artifacts.

```mermaid
flowchart LR
    User["User / Reviewer"]
    Repo["GitHub Repository"]
    Actions["GitHub Actions"]
    Webhook["GitHub Webhook"]
    Backend["FastAPI Backend Services"]
    CLI["CodeScribe CLI"]
    Workflow["Documentation Workflow"]
    AST["AST Parser"]
    Security["Security Scanner"]
    Intelligence["PR Intelligence Engine"]
    LLM["LLM Analysis Layer"]
    Docs["Documentation Generator"]
    Review["AI Review Agent"]
    Approval["Human Approval Workflow"]
    DB["PostgreSQL / SQLite Metadata Store"]
    Redis["Redis"]
    Comments["Pull Request Comments"]
    Reports["Output Reports"]
    Feedback["Feedback and Metrics"]

    User --> Repo
    Repo --> Webhook
    Repo --> Actions
    Webhook --> Backend
    Actions --> CLI
    Backend --> Workflow
    CLI --> Workflow
    Workflow --> AST
    Workflow --> Security
    Workflow --> Intelligence
    Workflow --> LLM
    LLM --> Docs
    Intelligence --> Review
    Security --> Review
    AST --> Docs
    Docs --> Reports
    Review --> Reports
    Review --> Approval
    Approval --> Comments
    Workflow --> DB
    Backend --> Redis
    Approval --> Feedback
    Feedback --> DB
    Reports --> User
    Comments --> Repo
```

## System Components

- **FastAPI application**: `app/main.py` wires health, webhooks, pull request processing,
  artifacts, approvals, PR intelligence, review, feedback, and metrics routes.
- **GitHub integration**: `app/services/github.py` fetches PR files and diffs and can create PR
  comments or reviews when `GITHUB_TOKEN` and publishing options are enabled.
- **Webhook ingestion**: `app/api/routes/webhooks.py` validates GitHub signatures outside
  local/dev/test environments, accepts PR events, and queues background processing.
- **AST and symbol analysis**: `app/services/ast_analysis.py` delegates to Python AST parsing and
  pattern-based parsers for Go, Java, and TypeScript.
- **LLM provider layer**: `app/services/llm_providers.py` implements `BaseLLMProvider`,
  `OllamaProvider`, optional `GeminiProvider`, and deterministic `LocalFallbackProvider`.
- **Documentation generator**: `app/services/generators.py` creates function, class, module, PR,
  and release-note documentation.
- **PR intelligence engine**: `app/services/pr_intelligence.py` classifies changes, calculates
  risk and quality scores, detects security findings, builds dependency signals, and produces
  Markdown reports.
- **Review agent**: `app/services/review_agent.py` generates review decisions, confidence scores,
  inline comments, and `review_report.md`.
- **Review publishing**: `app/services/review_publishing.py` publishes approved comments and review
  summaries to GitHub, or performs dry-run behavior without a token.
- **Feedback evaluation**: `app/services/feedback_evaluation.py` stores human feedback, calculates
  acceptance/rejection/agreement metrics, writes `feedback_report.md`, and exports JSONL learning
  data.
- **Database layer**: `app/db/models.py` and `app/db/repository.py` define and persist PR metadata,
  files, artifacts, validations, approvals, reviews, comments, feedback, and metrics.

## End-to-End Workflow

```mermaid
sequenceDiagram
    participant Reviewer as User / Reviewer
    participant GitHub as GitHub Repository
    participant Action as GitHub Actions
    participant API as FastAPI Backend
    participant Parser as AST Parser
    participant Scanner as Security Scanner
    participant LLM as LLM Analysis Layer
    participant Generator as Documentation Generator
    participant DB as Metadata Store
    participant Output as Reports / PR Comments

    Reviewer->>GitHub: Open or update pull request
    GitHub->>API: Webhook event
    GitHub->>Action: pull_request workflow
    API->>GitHub: Fetch changed files and diff
    Action->>Action: Extract local git diff
    API->>Parser: Analyze changed code
    Action->>Parser: Analyze changed code
    Parser->>Scanner: Provide file and symbol context
    Scanner->>LLM: Add risk and security context
    LLM->>Generator: Return structured JSON
    Generator->>DB: Store artifacts, reviews, metrics
    Generator->>Output: Write Markdown reports
    Reviewer->>API: Approve, request changes, or submit feedback
    API->>GitHub: Optionally publish approved comments
```

The generated report set includes:

- `documentation_report.md`
- `pr_summary.md`
- `risk_report.md`
- `security_report.md`
- `impact_analysis.md`
- `quality_report.md`
- `review_report.md`

## Installation Instructions

Prerequisites:

- Python 3.11 or newer.
- Git.
- Optional: Ollama for local LLM output.
- Optional: Docker Desktop or another Docker-compatible runtime for the full container stack.

Install the Python package and development dependencies:

```bash
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev]"
```

Create local configuration:

```bash
cp .env.example .env
```

The checked-in `.env.example` is safe to publish and uses local development defaults. Do not commit
real `.env` files, tokens, webhook secrets, database dumps, logs, or generated artifacts.

## Local Development Setup

Run the FastAPI server locally:

```bash
make dev
```

Open:

- Health: <http://127.0.0.1:8000/health>
- API docs: <http://127.0.0.1:8000/docs>

Run the local verification gates:

```bash
python3 -m pytest
python3 -m ruff check .
python3 -m compileall -q app scripts tests
```

Run a sample PR through the local API:

```bash
python3 scripts/smoke_process_pr.py
```

If the server is on another port:

```bash
CODESCRIBE_BASE_URL=http://127.0.0.1:8001 python3 scripts/smoke_process_pr.py
```

## Ollama Setup

Install and start Ollama:

```bash
brew install ollama
ollama serve
ollama pull qwen3:8b
```

Default settings:

- `LLM_PROVIDER=ollama`
- `OLLAMA_BASE_URL=http://localhost:11434`
- `OLLAMA_MODEL=qwen3:8b`

If Ollama is not reachable, CodeScribe logs a warning and uses deterministic local output.

## Docker Setup

Run the full local stack:

```bash
cp .env.example .env
docker compose up --build
```

Services:

- `api`: FastAPI application on port `8000`.
- `postgres`: PostgreSQL 16 on port `5432`.
- `redis`: Redis 7 on port `6379`.
- `ollama`: Ollama on port `11434`.

Persistent volumes:

- `postgres_data`
- `ollama_data`

Pull the default Ollama model through Compose:

```bash
docker compose --profile models up ollama-pull
```

Docker Compose overrides `DATABASE_URL` to use PostgreSQL inside the container network:

```text
postgresql+asyncpg://codescribe:codescribe@postgres:5432/codescribe
```

## Configuration and Environment Variables

Important settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `local` | Runtime environment. Webhook signatures are enforced outside local/dev/test. |
| `API_PREFIX` | `/api/v1` | Prefix for API routes. |
| `CODESCRIBE_MODE` | `webhook_server` | Runtime mode, such as `webhook_server` or `github_action`. |
| `DATABASE_URL` | `sqlite+aiosqlite:///./codescribe.db` | Async SQLAlchemy database URL for local dev. |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis URL reserved for async/background workflow support. |
| `GITHUB_WEBHOOK_SECRET` | `replace-me` | GitHub webhook signature secret. |
| `GITHUB_TOKEN` | empty | Optional token for GitHub API reads and comment/review publishing. |
| `LLM_PROVIDER` | `ollama` | `ollama`, `gemini`, or `local_fallback`. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint. |
| `OLLAMA_MODEL` | `qwen3:8b` | Ollama model name. |
| `LLM_REQUEST_TIMEOUT_SECONDS` | `60` | Timeout for LLM calls. |
| `LLM_MAX_RETRIES` | `2` | LLM retry attempts before fallback. |
| `AUTO_POST_REVIEWS` | `false` | Publish GitHub reviews automatically after generation. |
| `POST_PR_COMMENT` | `false` | Post summary comments in GitHub Actions mode. |
| `TRAINING_DATASET_PATH` | `outputs/training_dataset.jsonl` | Feedback learning dataset export path. |
| `GEMINI_API_KEY` | empty | Optional Gemini key when `LLM_PROVIDER=gemini`. |
| `PUBLISH_MODE` | `dry_run` | Publishing mode metadata; comment posting still requires explicit enablement. |

## GitHub Webhook Setup

1. Deploy CodeScribe where GitHub can reach it.
2. Set `APP_ENV=production`.
3. Set a strong `GITHUB_WEBHOOK_SECRET`.
4. Add a repository webhook in GitHub:
   - Payload URL: `https://your-host/api/v1/webhooks/github`
   - Content type: `application/json`
   - Secret: same value as `GITHUB_WEBHOOK_SECRET`
   - Events: pull request events
5. Optional publishing:
   - Set `GITHUB_TOKEN` with pull request read/write permissions.
   - Keep `AUTO_POST_REVIEWS=false` unless automatic publishing is intentionally enabled.

## GitHub Actions Mode

Use CodeScribe without a hosted webhook service:

```yaml
name: CodeScribe
on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write

jobs:
  codescribe:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: Lokesh-Venkatesh-21/codescribe@main
        with:
          post-comment: false
          output-dir: codescribe-reports
      - uses: actions/upload-artifact@v4
        with:
          name: codescribe-reports
          path: codescribe-reports
```

Safe defaults:

- PR comments are disabled unless `post-comment: true`.
- Reviews are not auto-approved.
- Requesting changes is not posted automatically unless publishing is explicitly enabled.
- Reports are generated by default.

## Example Usage

Run the CLI against a local pull request diff:

```bash
codescribe analyze-pr \
  --repo acme/widgets \
  --pr-number 42 \
  --base-ref origin/main \
  --head-ref HEAD \
  --output-dir codescribe-reports \
  --post-comment false
```

Process a PR payload through the API:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/pull-requests/process \
  -H "Content-Type: application/json" \
  -d '{
    "repo_full_name": "acme/widgets",
    "pr_number": 42,
    "head_sha": "abc123",
    "title": "Add widget pricing service",
    "author": "octocat",
    "files": [
      {
        "filename": "app/pricing.py",
        "status": "added",
        "patch": "@@\n+class PricingService:\n+    def quote(self, sku, quantity):\n+        return quantity * 10\n",
        "additions": 3,
        "deletions": 0
      }
    ]
  }'
```

Read generated outputs:

```bash
curl http://127.0.0.1:8000/api/v1/pr/{pull_request_id}/summary
curl http://127.0.0.1:8000/api/v1/pr/{pull_request_id}/risk
curl http://127.0.0.1:8000/api/v1/pr/{pull_request_id}/security
curl http://127.0.0.1:8000/api/v1/pr/{pull_request_id}/quality
curl http://127.0.0.1:8000/api/v1/pr/{pull_request_id}/review
```

Approve and publish a generated review:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/pr/{pull_request_id}/approve
curl -X POST http://127.0.0.1:8000/api/v1/pr/{pull_request_id}/publish-review
```

Submit human feedback:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/review/{review_id}/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "human_reviewer_decision": "NEEDS_HUMAN_REVIEW",
    "outcome": "accepted",
    "reviewer": "docs-lead",
    "team": "platform",
    "notes": "The testing recommendation was useful."
  }'
```

Read metrics:

```bash
curl http://127.0.0.1:8000/api/v1/metrics
curl http://127.0.0.1:8000/api/v1/metrics/accuracy
curl http://127.0.0.1:8000/api/v1/metrics/reviewer-agreement
```

## Future Enhancements

- Replace in-process background tasks with durable Redis-backed workers.
- Add Alembic migration generation and migration checks to CI.
- Add authenticated dashboard views for approvals, metrics, and report exploration.
- Add richer parsers through tree-sitter for Go, Java, and TypeScript.
- Add request tracing, prompt version management, and token/cost accounting.
- Add SARIF export for security findings.
- Add mypy or pyright typing checks after the public API stabilizes.
- Add optional GitHub App authentication for installation-scoped repository access.
- Add evaluation suites using real historical PRs and reviewer outcomes.

## Development

Common commands:

```bash
make install
make dev
make test
make lint
```

Before pushing:

```bash
python3 -m pytest
python3 -m ruff check .
python3 -m compileall -q app scripts tests
```

## Troubleshooting

- **Ollama is unavailable**: CodeScribe logs a warning and uses deterministic fallback output.
- **Webhook returns 401**: verify `APP_ENV`, `GITHUB_WEBHOOK_SECRET`, and GitHub's webhook secret.
- **No GitHub comments are posted**: set `GITHUB_TOKEN` and opt into publishing.
- **GitHub Actions diff is empty**: ensure `actions/checkout` uses `fetch-depth: 0`.
- **Docker model is missing**: run `docker compose --profile models up ollama-pull`.
- **Local smoke script cannot connect**: confirm the API is running and set `CODESCRIBE_BASE_URL`
  when using a non-default port.
