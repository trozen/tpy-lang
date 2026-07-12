"""Static progress measurement for deleting THIR predictive admission."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


_LOWER = Path("tpyc/thir/lower")
_EXPR_GATES = _LOWER / "expr_gates.py"

_REMOVED_PREFLIGHTS = frozenset({
    "_body_eligible", "_stmt_eligible", "_expr_eligible",
    "_raise_eligible", "_while_eligible", "_assert_eligible",
    "_tuple_unpack_eligible", "_expr_stmt_eligible", "_assign_eligible",
    "_aug_assign_eligible", "_return_eligible", "_var_decl_eligible",
    "_if_eligible", "_narrow_if_eligible", "_with_eligible",
    "_try_eligible", "_match_eligible", "_for_each_stmt_eligible",
    "_gate_leaf", "_leaf_shape_reject",
    "_comp_decl_ok", "_comp_print_arg_ok",
    "_comp_decl_plan", "_comp_print_arg_plan",
    "_for_range_eligible", "_for_each_container_eligible",
    "_for_tuple_unpack_eligible", "_classify_for_each",
    "_for_range_plan", "_for_each_container_plan",
    "_for_tuple_unpack_plan", "_classify_match", "_match_plan",
    "_function_eligible", "_f1_param_eligible", "_ctor_param_eligible",
    "_eligible_return",
})


@dataclass(frozen=True)
class GateScorecard:
    preflight_symbols: tuple[str, ...]
    expression_gate_lines: int
    expression_gate_imports: int
    expression_gate_consumers: tuple[tuple[str, int], ...]

    @property
    def expression_gate_present(self) -> bool:
        return self.expression_gate_lines > 0


def _defined_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _expr_gate_imports(path: Path) -> int:
    tree = ast.parse(path.read_text(), filename=str(path))
    return sum(
        len(node.names)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == "expr_gates"
    )


def collect_gate_scorecard(repo_root: Path | None = None) -> GateScorecard:
    root = repo_root or Path(__file__).parents[2]
    lower = root / _LOWER
    paths = tuple(lower.rglob("*.py"))
    defined_names = set().union(*(_defined_names(path) for path in paths))
    preflight_symbols = tuple(sorted(_REMOVED_PREFLIGHTS & defined_names))

    gate_path = root / _EXPR_GATES
    expression_gate_lines = (
        len(gate_path.read_text().splitlines()) if gate_path.exists() else 0)
    consumers = tuple(sorted(
        (str(path.relative_to(lower)), count)
        for path in paths
        if path != gate_path
        if (count := _expr_gate_imports(path))
    ))
    return GateScorecard(
        preflight_symbols=preflight_symbols,
        expression_gate_lines=expression_gate_lines,
        expression_gate_imports=sum(count for _, count in consumers),
        expression_gate_consumers=consumers,
    )


def format_gate_scorecard(score: GateScorecard) -> str:
    gate_state = "present" if score.expression_gate_present else "removed"
    lines = [
        "tpy| no-gate: "
        f"{len(score.preflight_symbols)} known predictive preflight symbols",
        "tpy| expression gates: "
        f"{gate_state}; {score.expression_gate_lines} lines; "
        f"{score.expression_gate_imports} imported symbols across "
        f"{len(score.expression_gate_consumers)} consumers",
    ]
    if score.preflight_symbols:
        lines.append("tpy| predictive preflights: "
                     + ", ".join(score.preflight_symbols))
    if score.expression_gate_consumers:
        lines.append("tpy| expression gate consumers: " + ", ".join(
            f"{path}={count}"
            for path, count in score.expression_gate_consumers))
    return "\n".join(lines)


if __name__ == "__main__":
    print(format_gate_scorecard(collect_gate_scorecard()))
