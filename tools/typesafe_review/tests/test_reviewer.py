import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.typesafe_review.cli import main
from tools.typesafe_review.runner import load_key, questions, run_jobs, write_reports
from tools.typesafe_review.scanner import discover, make_jobs, screen


class ReviewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_discovery_excludes_secret_generated_binary_vendor_files(self):
        self.put("app/good.py", "x = 1\n")
        self.put("data/generate_sample.py", "x = 1\n")
        for name in ["app/.env", "app/credentials.py", "app/generated/file.py",
                     "app/__pycache__/bad.py", "ui/web/vendor/lib.js", "app/bad.min.js",
                     "tests/fixtures/private.py", "app/.venv/lib.py", "app/key.pem"]:
            self.put(name, "DO_NOT_SEND")
        self.put("app/binary.py", "a\x00b")
        sources, skipped = discover(self.root)
        self.assertEqual(set(sources), {"app/good.py", "data/generate_sample.py"})
        self.assertTrue(any(s["reason"] == "binary" for s in skipped))

    def test_embedded_credentials_block_entire_file_without_returning_value(self):
        for value in ['password = "synthetic-password"',
                      'const apiKey = "synthetic-key";',
                      'url = "postgresql://user:synthetic-password@host/db"',
                      '# Authorization: Bearer abcdefghijklmnop',
                      'KEY = "sk-abcdefghijklmnopqrstuv123456"',
                      '-----BEGIN PRIVATE KEY-----',
                      'config = {"token": "synthetic-token"}',
                      'apiKey = `synthetic-backtick-key`;',
                      'password: synthetic-yaml-password',
                      'os.environ["SOME_SETTING"] = "private-value"']:
            self.assertFalse(screen(value), value)
        self.assertTrue(screen('token = settings.api_key\nx = os.getenv("API_KEY")'))
        self.assertTrue(screen('return {"token": token, "url": f"{base}/?invite={token}"}'))
        self.assertTrue(screen('headers.Authorization="Bearer "+authToken;'))
        self.assertTrue(screen('headers={"Authorization": f"Bearer {settings.api_key}"}'))

    def test_known_environment_values_block_payload(self):
        with patch.dict(os.environ, {"UNRELATED_SETTING": "synthetic-private-env-value"}):
            self.assertFalse(screen('text = "synthetic-private-env-value"'))

    def test_all_lines_covered_and_requests_bounded(self):
        content = "".join(f"x_{i} = {i}\n" for i in range(400))
        jobs, skipped = make_jobs({"app/main.py": content}, 7000)
        self.assertFalse(skipped)
        covered = set()
        for job in jobs:
            self.assertLessEqual(len(json.dumps({"state": job["state"], "questions": questions()}, ensure_ascii=True).encode()), 7000)
            for part in job["state"]["files"]:
                covered.update(range(part["start_line"], part["end_line"] + 1))
        self.assertEqual(covered, set(range(1, 401)))

    def test_related_flow_contains_multiple_files(self):
        jobs, _ = make_jobs({"app/auth.py": "def authenticate():\n    pass\n",
                             "app/api.py": "from app.auth import authenticate\n"}, 16000)
        groups = [j for j in jobs if j["kind"] == "flow"]
        self.assertTrue(groups)
        self.assertEqual(set(groups[0]["filenames"]), {"app/auth.py", "app/api.py"})

    def test_oversized_line_is_reported_not_silently_truncated(self):
        jobs, skipped = make_jobs({"app/a.py": "x='" + "a" * 20000 + "'"}, 7000)
        self.assertFalse(jobs)
        self.assertTrue(skipped)

    def test_sdk_results_and_request_cap_preserve_partial_status(self):
        jobs, _ = make_jobs({"app/a.py": "x=1", "app/b.py": "x=2"}, 16000)
        answer = SimpleNamespace(choice="not_detected", confidence=.8,
                                 probabilities={"detected": .1, "not_detected": .8, "insufficient_context": .1})
        client = SimpleNamespace(system_one=lambda **kw: SimpleNamespace(
            choices={k: answer for k in questions()}, model="fake-model", usage=None))
        report = run_jobs(jobs, [], client, lambda **kw: kw, max_requests=1)
        self.assertFalse(report["complete"])
        self.assertEqual(report["results"][0]["assessments"]["security_risk"]["confidence"], .8)
        self.assertEqual(report["results"][1]["status"], "not_reviewed")

    def test_exception_body_never_appears_in_report(self):
        def fail(**kw):
            raise RuntimeError("synthetic-sensitive-error-body")
        jobs, _ = make_jobs({"app/a.py": "x=1"}, 16000)
        report = run_jobs(jobs, [], SimpleNamespace(system_one=fail), lambda **kw: kw)
        self.assertNotIn("synthetic-sensitive", json.dumps(report))
        self.assertFalse(report["complete"])

    def test_auth_failure_stops_further_paid_attempts(self):
        class AuthFailure(Exception):
            status = 401

        def fail(**kw):
            raise AuthFailure("private-response")

        jobs, _ = make_jobs({"app/a.py": "x=1", "app/b.py": "x=2"}, 16000)
        report = run_jobs(jobs, [], SimpleNamespace(system_one=fail), lambda **kw: kw)
        self.assertEqual(report["attempted_requests"], 1)
        self.assertEqual(report["results"][1]["status"], "not_reviewed")

    def test_three_failures_stop_remaining_jobs(self):
        def fail(**kw):
            raise RuntimeError("network unavailable")
        jobs, _ = make_jobs({f"app/a{i}.py": "x=1" for i in range(5)}, 16000)
        report = run_jobs(jobs, [], SimpleNamespace(system_one=fail), lambda **kw: kw)
        self.assertEqual(report["attempted_requests"], 3)
        self.assertEqual(report["results"][-1]["status"], "not_reviewed")

    def test_missing_answers_are_failure_not_clean(self):
        jobs, _ = make_jobs({"app/a.py": "x=1"}, 16000)
        client = SimpleNamespace(system_one=lambda **kw: SimpleNamespace(choices={}, model="fake"))
        report = run_jobs(jobs, [], client, lambda **kw: kw)
        self.assertEqual(report["results"][0]["status"], "error")

    def test_key_loading_does_not_export_other_dotenv_values(self):
        self.put(".env", "TYPESAFE_API_KEY=synthetic-test-key\nPRIVATE_SETTING=do-not-export\n")
        with patch.dict(os.environ, {}, clear=True):
            load_key(self.root)
            self.assertEqual(os.environ["TYPESAFE_API_KEY"], "synthetic-test-key")
            self.assertNotIn("PRIVATE_SETTING", os.environ)
            self.assertFalse(screen('text="do-not-export"'))
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "already-set"}):
            load_key(self.root)
            self.assertEqual(os.environ["TYPESAFE_API_KEY"], "already-set")

    def test_reports_omit_source(self):
        jobs, _ = make_jobs({"app/a.py": "unique_source_marker=1"}, 16000)
        report = run_jobs(jobs, [], None, None, dry_run=True)
        write_reports(self.root, report)
        for name in ["typesafe_review_plan.json", "typesafe_review_plan.md"]:
            text = (self.root / name).read_text(encoding="utf-8")
            self.assertNotIn("unique_source_marker", text)
            self.assertIn("app/a.py", text)

    def test_dry_run_needs_no_key_or_sdk(self):
        self.put("app/a.py", "x=1")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(main(["--root", str(self.root), "--dry-run"]), 0)

    def test_unicode_and_long_lines_stay_within_sdk_wire_budget(self):
        from httpx2 import MockTransport, Response
        from typesafe_sdk import Choice, RetryPolicy, TypeSafeClient

        source = "".join(f"row_{i} = '界界界界界界'\n" for i in range(200))
        jobs, skipped = make_jobs({"app/a.py": source}, 7000)
        wire_sizes = []

        def handle(request):
            wire_sizes.append(len(request.content))
            body = json.loads(request.content)
            self.assertEqual(request.url.host, "api.typesafe.ai")
            self.assertNotIn("synthetic-sdk-test-key", request.content.decode())
            self.assertIn("source", body["state"]["files"][0])
            return Response(200, json={"model": "jev-test", "usage": {"input_tokens": 10, "output_tokens": 0},
                                      "answers": {k: {"type": "choice", "choice": "not_detected", "confidence": .8,
                                                      "probabilities": {"detected": .1, "not_detected": .8, "insufficient_context": .1}}
                                                  for k in body["questions"]}})

        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "synthetic-sdk-test-key", "TYPESAFE_LOG_LEVEL": "off"}):
            with TypeSafeClient(transport=MockTransport(handle), retry=RetryPolicy(max_retries=0),
                                base_url="https://api.typesafe.ai") as client:
                report = run_jobs(jobs, skipped, client, Choice)
        self.assertTrue(report["complete"])
        self.assertTrue(wire_sizes)
        self.assertLessEqual(max(wire_sizes), 7000)
        self.assertEqual(report["results"][0]["usage"]["input_tokens"], 10)

    def test_symlink_file_not_read(self):
        original = self.put("outside.py", "private=1")
        link = self.root / "app/link.py"
        link.parent.mkdir()
        try:
            link.symlink_to(original)
        except OSError:
            self.skipTest("OS does not permit symlink creation")
        sources, skipped = discover(self.root)
        self.assertFalse(sources)
        self.assertEqual(skipped[0]["reason"], "symlink_or_outside_root")

    def test_final_report_persists_categories_without_source(self):
        jobs, _ = make_jobs({"app/a.py": "unique_source_marker=1"}, 16000)
        positive = SimpleNamespace(choice="detected", confidence=.9,
                                   probabilities={"detected": .9, "not_detected": .05, "insufficient_context": .05})
        client = SimpleNamespace(system_one=lambda **kw: SimpleNamespace(
            choices={k: positive for k in questions()}, model="fake", usage=None))
        report = run_jobs(jobs, [], client, lambda **kw: kw)
        write_reports(self.root, report)
        saved = (self.root / "typesafe_review_report.json").read_text()
        self.assertNotIn("unique_source_marker", saved)
        self.assertIn("security_risk", json.loads(saved)["results"][0]["categories"])
        self.assertFalse(json.loads(saved)["results"][0]["no_major_issue"])

    def test_environment_profile_values_are_never_exported_or_sent(self):
        self.put(".env", "TYPESAFE_API_KEY=synthetic-only-key\n")
        self.put(".env.prod", "PASSWORD=synthetic-profile-password\n")
        with patch.dict(os.environ, {}, clear=True):
            load_key(self.root)
            self.assertFalse(screen('message = "synthetic-profile-password"'))
            self.assertNotIn("PASSWORD", os.environ)


if __name__ == "__main__":
    unittest.main()
