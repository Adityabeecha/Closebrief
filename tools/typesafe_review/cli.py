"""Manual CLI; dry-run never loads credentials or imports the SDK."""

import argparse
import logging
import os
from pathlib import Path

from .runner import load_key, run_jobs, write_reports
from .scanner import discover, make_jobs


def main(argv=None):
    parser = argparse.ArgumentParser(description="Review Closebrief source with TypeSafe AI.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--dry-run", action="store_true", help="Offline manifest; never loads credentials or contacts TypeSafe")
    parser.add_argument("--max-requests", type=int, help="Limit review requests (SDK retries may add HTTP attempts)")
    parser.add_argument("--request-bytes", type=int, default=16000, help="Serialized request budget, 7000–24000")
    args = parser.parse_args(argv)
    if not 7000 <= args.request_bytes <= 24000 or (args.max_requests is not None and args.max_requests < 1):
        parser.error("request-bytes must be 7000–24000 and max-requests must be positive")
    root = args.root.resolve()
    if not (root / "app").is_dir():
        parser.error("root must contain the app directory")
    try:
        if not args.dry_run:
            load_key(root)
        sources, skipped = discover(root)
        jobs, chunk_skips = make_jobs(sources, args.request_bytes)
        skipped += chunk_skips
        print(f"Eligible files: {len(sources)}; planned requests: {len(jobs)}; skipped items: {len(skipped)}.")
        if args.dry_run:
            report = run_jobs(jobs, skipped, None, None, dry_run=True)
        else:
            # Set before importing: debug SDK logging includes unredacted bodies.
            os.environ["TYPESAFE_LOG_LEVEL"] = "off"
            for name in ("typesafe_sdk", "httpx2", "httpcore2", "httpcore", "httpx"):
                logging.getLogger(name).disabled = True
            from typesafe_sdk import Choice, RetryPolicy, TypeSafeClient
            # Lock destination to the official service, ignoring ambient BASE_URL.
            with TypeSafeClient(base_url="https://api.typesafe.ai", model="jev-latest", timeout=60,
                                retry=RetryPolicy(max_retries=2)) as client:
                report = run_jobs(jobs, skipped, client, Choice, args.max_requests,
                                  checkpoint=lambda current: write_reports(root, current))
        write_reports(root, report)
        prefix = "typesafe_review_plan" if args.dry_run else "typesafe_review_report"
        print(f"Saved {prefix}.json and {prefix}.md. Complete coverage: {report['complete']}.")
        return 0 if args.dry_run or report["complete"] else 2
    except KeyboardInterrupt:
        print("Review interrupted; any completed checkpoints remain in the report.")
        return 130
    except Exception:
        print("Review could not run. Check the isolated SDK installation, TYPESAFE_API_KEY, network, and file permissions. "
              "Error details are suppressed to protect credentials.")
        return 1
