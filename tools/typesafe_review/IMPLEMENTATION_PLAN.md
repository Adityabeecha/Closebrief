# TypeSafe reviewer implementation plan

Goal: implement the standalone reviewer approved in chat, without changing application behavior.
Architecture: local allowlisted discovery and credential screening → bounded per-file and related-flow requests → official SDK → metadata-only reports.
Stack: Python 3.11+, typesafe-sdk, python-dotenv, standard-library unittest.
Spec: the user-approved repository assessment and design in this task.

1. Write offline safety and integration tests in tests/test_reviewer.py. Verify missing implementation fails.
2. Implement scanner.py: explicit source roots, deny rules, symlink rejection, secret screening, source line coverage, bounded contexts. Tests must catch excluded secrets, embedded credentials, oversized lines, missing source, and incomplete coverage.
3. Implement runner.py and cli.py: SDK Choice questions, validated response fields, environment-only authentication, dry-run, request cap, safe errors, atomic JSON/Markdown reports. Tests must catch credential leakage, response errors, partial runs, and accidental network calls in dry-run.
4. Add root entry point, isolated requirements, usage documentation, and ignore rules. Install official SDK in .venv-typesafe, never the app environment.
5. Run offline tests, lint, pip check, SDK mock-transport compatibility, repository dry-run and git diff checks. Do not perform live paid requests or commit files.

Acceptance: no application imports or execution by the reviewer; no env file in a payload; no raw API errors or bodies in reports; every discovered eligible file has explicit coverage; all outbound requests fit the configured serialized byte budget; cross-file results list every participating file.
