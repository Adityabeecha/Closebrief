"""Prompt A/B harness (v3.0). Run two prompt variants over the golden set and
pick a winner by faithfulness (gate) + groundedness.

  python -m eval.ab                       # baseline vs concise
  python -m eval.ab --variants baseline concise
  python -m eval.ab --out eval/ab_results.json

Promotion is human-gated on purpose: the harness *recommends*; to promote, point
SYSTEM_PROMPT at the winning variant in a reviewed PR. --promote only prints the
one-line change to make (it never edits prod prompts for you).

Needs an LLM (OPENAI_API_KEY / provider config) — runs in CI where the key lives.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from app.experiments import pick_winner, score_variant
from app.generation.guard import check_faithfulness
from app.generation.prompts import PROMPT_VARIANTS
from app.retrieval.retrieve import retrieve
from app.schemas import ComputedFact, ContextSnippet
from eval.run import build_case_store, load_cases


def _score_over_cases(cases, system_prompt: str, tmp_dir: Path, k: int = 5) -> dict:
    from app.generation.generate import GenerationFailedFactsOnly, generate_insight
    from app.generation.llm_client import get_llm_client

    llm = get_llm_client()
    faithful = grounded = total = 0
    cost = 0.0
    for case in cases:
        fact = ComputedFact(**case["fact"])
        store, embedder, vs, _ = build_case_store(case, tmp_dir)
        chunks = retrieve(fact.metric, fact.period, store, embedder, vs, k=k)
        context = [ContextSnippet(id=f"ctx_{c.id:03d}", type=c.type, title=c.title, body=c.body)
                   for c in chunks]
        try:
            insight = generate_insight(fact, context, llm, [c.score for c in chunks],
                                       system_prompt=system_prompt)
        except GenerationFailedFactsOnly:
            continue
        total += 1
        cost += insight.cost_usd or 0.0
        narrative = insight.narrative or ""
        if check_faithfulness(narrative, fact, context)[0]:
            faithful += 1
        # Groundedness: any cause keyword used must appear in a retrieved chunk.
        kws = [kw.lower() for kw in case.get("cause_keywords", [])]
        nl = narrative.lower()
        chunk_text = " ".join((c.body + " " + c.title).lower() for c in chunks)
        used = [kw for kw in kws if kw in nl]
        if not [kw for kw in used if kw not in chunk_text]:
            grounded += 1
    return score_variant(faithful, grounded, total, cost)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="+", default=["baseline", "concise"])
    ap.add_argument("--out", default=None, help="write results JSON to this path")
    ap.add_argument("--promote", action="store_true",
                    help="print the one-line change to promote the winner (does not edit)")
    args = ap.parse_args()

    unknown = [v for v in args.variants if v not in PROMPT_VARIANTS]
    if unknown:
        print(f"Unknown variant(s): {unknown}. Known: {list(PROMPT_VARIANTS)}", file=sys.stderr)
        return 2

    cases = load_cases()
    scores: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name in args.variants:
            scores[name] = _score_over_cases(cases, PROMPT_VARIANTS[name], tmp)
            s = scores[name]
            print(f"[{name}] faithfulness={s['faithfulness']:.0%} "
                  f"groundedness={s['groundedness']:.0%} cost=${s['cost_usd']:.4f} n={s['n']}")

    decision = pick_winner(scores)
    print("\n" + "=" * 60)
    print(f"WINNER: {decision['winner'] or '(none)'}"
          f"  [{'conclusive' if decision['conclusive'] else 'INCONCLUSIVE'}]")
    print(decision["reason"])

    result = {"scores": scores, "decision": decision}
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    if args.promote and decision["winner"]:
        print(f"\nTo promote (human-gated): in app/generation/prompts.py set\n"
              f"    SYSTEM_PROMPT = PROMPT_VARIANTS[\"{decision['winner']}\"]\n"
              f"and open a PR — a prompt change is a behavior change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
