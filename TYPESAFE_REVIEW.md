# Review Closebrief with TypeSafe AI

This is a standalone, manual code checker. It reads source text without importing
or starting Closebrief, connecting to its database, or changing app functionality.
The official `typesafe-sdk==0.7.0` runs in a separate Python environment.

## Run on Windows

From the `closebrief` directory, add this entry to your existing `.env` yourself:

```dotenv
TYPESAFE_API_KEY=<your key>
```

Do not paste your key in chat or put it in source code. `.env` is already ignored.

The reviewer environment is installed in this workspace. Run:

```powershell
.\.venv-typesafe\Scripts\python.exe review_with_typesafe.py --dry-run
.\.venv-typesafe\Scripts\python.exe review_with_typesafe.py --max-requests 5
.\.venv-typesafe\Scripts\python.exe review_with_typesafe.py
```

The second command is an optional small **paid** trial; the last runs all planned
requests. Each run starts fresh and replaces its previous reports; there is no
resume/cache. A full run can make hundreds of requests. Review the dry-run plan
first. SDK retries can add up to two HTTP attempts per review request.

Alternatively activate `.venv-typesafe` and use `python review_with_typesafe.py`.
The script resolves its project root relative to itself, regardless of the shell's
working directory. `--root` selects another Closebrief checkout explicitly.

On a fresh checkout (Python 3.11+):

```powershell
python -m venv .venv-typesafe
.\.venv-typesafe\Scripts\python.exe -m pip install -r requirements-typesafe-review.txt
```

The application environment and `pyproject.toml` dependencies remain separate.

## Scope and context

Included: Python/SQL in `app/`, first-party HTML/CSS/JS in `ui/web/`, test code,
evaluation scripts, selected Docker/Render/GitHub/package configuration, and
`data/generate_sample.py`. New, untracked files in these source roots are included.

Excluded: `.env*` (including `.env.example`), secret-named files, private-key/binary
formats, symlinks, `.git`, virtual environments, dependencies, vendor libraries,
generated/minified files, builds, caches, test fixtures, database files, uploads,
datasets, `real_testing/`, logs, `graphify-out/`, and the reviewer itself. New source
roots/configuration files need an explicit addition to `ROOTS`/`CONFIGS` in
`tools/typesafe_review/scanner.py`; arbitrary Markdown/JSON files are not uploaded.

Source files are screened locally. A suspected embedded credential blocks the
entire file, including test files containing dummy credentials. There is no unsafe
override flag. Move real credentials into environment configuration before reviewing
that file. Heuristic screening can produce false positives and cannot establish
that arbitrary source is secret-free.

Dry-run never loads `.env` or contacts TypeSafe. During a live run, `.env` and local
`.env.*` profiles are parsed in memory: only `TYPESAFE_API_KEY` from `.env` is exported
to the process, and an existing process key takes precedence. Other values of eight
or more characters are used only as a local deny list, alongside process environment
values. They never become payload context. Profile files do not select the API key.
Consequently a live run may block more files than the offline plan.

Requests contain a static architecture summary, filenames, source excerpts and
original line ranges. The default serialized budget is 16,000 bytes, including
questions and a serialization reserve. `--request-bytes` accepts 7,000–24,000.
Python is split near top-level AST boundaries where practical; other text splits
on lines. Oversized single lines are reported as unreviewed, never silently dropped.
The scanner refuses files larger than 2 MB or invalid UTF-8 text.

Every included nonempty file gets per-file chunk coverage. Additional bounded
adjacent-file pairs cover authentication, uploads/KPIs, dashboard/query code,
narrative grounding, database/tenancy, and delivery/scheduling. These use actual
excerpts, sometimes shortened to fit; the report records exact ranges. This is
partial cross-file context, not an exhaustive call graph or whole-program proof.

## Questions and reports

Each request asks five independent `Choice` questions:

- Runtime-error risk
- Logic/correctness risk
- Security risk
- Substantial maintainability risk
- Whether no major issue is evident in the supplied scope

Choices are `detected`, `not_detected`, and `insufficient_context`. Several risk
categories can be detected together. A `detected` answer to `no_major_issue` means
the opposite of a detected risk. The report only sets its combined `no_major_issue`
flag when no risk is detected and none of the questions lacks context.

TypeSafe returns classifications, **not explanatory bug reports or fixes**.
Confidence and probability distributions are copied from SDK answers. They are
not proof of defects, and chunk probabilities are not averaged into invented file
confidence. Continue to use Ruff, pytest and human review.

Outputs in the project root:

- Dry-run: `typesafe_review_plan.json` and `.md`
- Live run: `typesafe_review_report.json` and `.md`

Reports contain filenames/ranges, categories, confidence/probabilities, model,
token usage, skipped-source reasons and request status. No source excerpts,
credential values, raw API bodies or raw exception strings are saved. Reports and
temporary files are Git-ignored and excluded from Docker images. Reports checkpoint
after each job; Ctrl+C preserves completed checkpoints. `planned_requests` may
exceed the checkpoint's result count after interruption.

`complete` means planned review coverage completed, **not that the app is safe**.
Secret-blocked, unreadable or oversized source, failed requests, and request caps
prevent complete coverage. Empty/generated/non-source exclusions are intentional.
An `insufficient_context` answer remains visible even when all requests completed.

Exit codes: `0` for dry-run or completed coverage, `2` for partial coverage,
`1` for setup/report failure, `130` for interruption. Detected risks do not currently
change the exit code. CI integration is not enabled.

The destination is fixed to `https://api.typesafe.ai`, model alias `jev-latest`.
The SDK reads the key from the process environment for authentication only. SDK
debug/body logging is disabled. Requests have a 60-second timeout and at most two
retries. Authentication failures, exhausted rate limits, or three consecutive
request/response failures stop further requests. Later jobs remain unreviewed.

## Offline verification

```powershell
.\.venv-typesafe\Scripts\python.exe -m unittest discover -s tools/typesafe_review/tests -v
.\.venv-typesafe\Scripts\python.exe -m pip check
```

Tests use temporary synthetic source and fake credentials, plus the real SDK with
an in-memory HTTP transport. They do not load your `.env` or contact TypeSafe.

Official references: [Python SDK](https://github.com/typesafe-ai/typesafe-sdk-python),
[SDK usage](https://docs.typesafe.ai/sdk/python/usage),
[model limits](https://docs.typesafe.ai/models).
