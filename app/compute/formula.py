"""Safe formula evaluation for custom/derived KPIs (v5.0).

A formula references other metrics in brackets and uses +,-,*,/ and parentheses:

    Gross Margin % = ([Net Revenue] - [COGS]) / [Net Revenue] * 100

Evaluation never uses eval(): the expression is parsed with `ast` and walked,
allowing ONLY arithmetic on numbers and metric references. Anything else (calls,
attributes, names, comparisons) is rejected. Division by zero yields None
(the KPI is 'unavailable' that period) rather than raising. Because the value is
computed deterministically here, narratives about a derived KPI pass the
faithfulness guard exactly like any other computed fact.
"""

from __future__ import annotations

import ast
import re

_BRACKET = re.compile(r"\[([^\]]+)\]")


class FormulaError(ValueError):
    pass


def referenced_metrics(formula: str) -> list[str]:
    """Metric names referenced by the formula, in order, de-duplicated."""
    seen, out = set(), []
    for name in _BRACKET.findall(formula or ""):
        n = name.strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def validate(formula: str) -> list[str]:
    """Parse-check a formula (references + arithmetic only). Returns the metric
    references. Raises FormulaError on anything unsafe or malformed."""
    refs = referenced_metrics(formula)
    if not refs:
        raise FormulaError("Formula must reference at least one metric, e.g. [Net Revenue]")
    # Evaluate against dummy values to surface parse/structure errors up front.
    evaluate(formula, {r: 1.0 for r in refs})
    return refs


_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)


def evaluate(formula: str, values: dict[str, float]) -> float | None:
    """Evaluate `formula` with metric values. Missing reference → FormulaError;
    division by zero → None."""
    tokens: dict[str, str] = {}   # var token -> metric name

    def _sub(m: re.Match) -> str:
        key = f"__m{len(tokens)}"
        tokens[key] = m.group(1).strip()
        return key

    expr = _BRACKET.sub(_sub, formula or "")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"Invalid formula syntax: {e.msg}") from e

    def _ev(node):
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            metric = tokens.get(node.id)
            if metric is None:
                raise FormulaError("Only [metric] references and numbers are allowed")
            if metric not in values or values[metric] is None:
                raise FormulaError(f"No value for referenced metric '{metric}'")
            return float(values[metric])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            v = _ev(node.operand)
            if v is None:
                return None
            return v if isinstance(node.op, ast.UAdd) else -v
        if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
            left, right = _ev(node.left), _ev(node.right)
            if left is None or right is None:   # division-by-zero propagates
                return None
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                return None   # division by zero → unavailable this period
            return left / right
        raise FormulaError("Only arithmetic (+ - * /) on metrics and numbers is allowed")

    return _ev(tree)
