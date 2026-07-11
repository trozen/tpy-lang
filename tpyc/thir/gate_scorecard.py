"""Static progress measurement for deleting THIR predictive admission."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


_LOWER = Path("tpyc/thir/lower")

_MILESTONES: tuple[tuple[str, frozenset[str], frozenset[Path]], ...] = (
    ("root traversals", frozenset({
        "_body_eligible", "_stmt_eligible", "_expr_eligible",
    }), frozenset()),
    ("statement preflights", frozenset({
        "_raise_eligible", "_while_eligible", "_assert_eligible",
        "_tuple_unpack_eligible", "_expr_stmt_eligible", "_assign_eligible",
        "_aug_assign_eligible", "_return_eligible", "_var_decl_eligible",
        "_if_eligible", "_narrow_if_eligible", "_with_eligible",
        "_try_eligible", "_match_eligible", "_for_each_stmt_eligible",
    }), frozenset()),
    ("resumable leaf preflight", frozenset({
        "_gate_leaf", "_leaf_shape_reject",
    }), frozenset()),
    ("comprehension preflight", frozenset({
        "_comp_decl_ok", "_comp_print_arg_ok",
        "_comp_decl_plan", "_comp_print_arg_plan",
    }), frozenset()),
    ("foreach preflight", frozenset({
        "_for_range_eligible", "_for_each_container_eligible",
        "_for_tuple_unpack_eligible",
        "_classify_for_each", "_for_range_plan",
        "_for_each_container_plan", "_for_tuple_unpack_plan",
    }), frozenset()),
    ("match prevalidation", frozenset({
        "_classify_match", "_match_plan",
    }), frozenset()),
    ("expression gate layer", frozenset(), frozenset({
        _LOWER / "expr_gates.py",
    })),
    ("callable preflights", frozenset({
        "_function_eligible", "_f1_param_eligible", "_ctor_param_eligible",
        "_eligible_return",
    }), frozenset()),
)

_FACT_QUERY_NAMES = frozenset({
    "_eligible_scalar", "_eligible_value_union", "_eligible_ptr_union",
    "_eligible_char", "_eligible_enum", "_eligible_ptr_value",
})


@dataclass(frozen=True)
class GateScorecard:
    completed: tuple[str, ...]
    remaining: tuple[str, ...]
    remaining_symbols: int
    remaining_lines: int
    policy_helpers: int


def _function_lines(path: Path) -> dict[str, int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return {
        node.name: node.end_lineno - node.lineno + 1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.end_lineno is not None
    }


def collect_gate_scorecard(repo_root: Path | None = None) -> GateScorecard:
    root = repo_root or Path(__file__).parents[2]
    # Keyed by (file, name) so a name reused across two lower/*.py files is
    # counted per-definition, not silently overwritten by dict.update.
    definitions: dict[tuple[Path, str], int] = {}
    for path in (root / _LOWER).rglob("*.py"):
        for name, lines in _function_lines(path).items():
            definitions[(path, name)] = lines
    defined_names = {name for _, name in definitions}

    completed: list[str] = []
    remaining: list[str] = []
    remaining_symbols = 0
    remaining_lines = 0
    for name, symbols, files in _MILESTONES:
        present = symbols & defined_names
        present_files = tuple(path for path in files if (root / path).exists())
        if present or present_files:
            remaining.append(name)
            remaining_symbols += len(present)
            remaining_lines += sum(
                lines for (_, sym), lines in definitions.items()
                if sym in present)
            remaining_lines += sum(
                len((root / path).read_text().splitlines())
                for path in present_files)
        else:
            completed.append(name)

    policy_helpers = sum(
        name.endswith("_eligible") and name not in _FACT_QUERY_NAMES
        for _, name in definitions
    )
    return GateScorecard(
        completed=tuple(completed),
        remaining=tuple(remaining),
        remaining_symbols=remaining_symbols,
        remaining_lines=remaining_lines,
        policy_helpers=policy_helpers,
    )


def format_gate_scorecard(score: GateScorecard) -> str:
    total = len(score.completed) + len(score.remaining)
    return "\n".join((
        f"tpy| no-gate: {len(score.completed)}/{total} milestones complete",
        f"tpy| no-gate: {score.remaining_symbols} preflight symbols; "
        f"{score.policy_helpers} policy helpers; "
        f"{score.remaining_lines} gate lines",
        "tpy| no-gate remaining: " + ", ".join(score.remaining),
    ))


if __name__ == "__main__":
    print(format_gate_scorecard(collect_gate_scorecard()))
