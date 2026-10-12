"""Phase 2 starts from the direct facts, never from the initial estimate."""

from ..typesys import INT32, FunctionInfo, MutationCallEdge, ParamInfo, make_list
from .mutation_propagation import propagate_mutation_facts


def _fn(name: str) -> FunctionInfo:
    fi = FunctionInfo(name=name, params=[ParamInfo("xs", make_list(INT32))],
                      return_type=INT32)
    fi.direct_mutated_params = frozenset()
    fi.direct_structural_mutated_params = frozenset()
    fi.direct_elem_mutated_params = frozenset()
    fi.direct_self_mutated = False
    # The over-approximate estimate a body leaves for readers before Phase 2
    # (a receiver call deferred to its edge).
    fi.mutated_params = frozenset({0})
    return fi


def test_call_cycle_does_not_refold_from_the_estimate() -> None:
    f, g = _fn("f"), _fn("g")
    f.call_edges = [MutationCallEdge(callee_fi=g, param_map={0: 0})]
    g.call_edges = [MutationCallEdge(callee_fi=f, param_map={0: 0})]
    propagate_mutation_facts([f, g])
    assert f.mutated_params == frozenset()
    assert g.mutated_params == frozenset()


def test_single_function_replaces_the_estimate() -> None:
    f = _fn("f")
    f.call_edges = []
    propagate_mutation_facts([f])
    assert f.mutated_params == frozenset()
