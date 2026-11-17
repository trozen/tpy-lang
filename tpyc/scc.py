"""Strongly connected components of a directed graph, the one helper every
pass that condenses a graph uses."""

from collections.abc import Callable, Hashable, Iterable
from typing import TypeVar

N = TypeVar("N", bound=Hashable)


def strongly_connected_components(roots: Iterable[N], successors: Callable[[N], Iterable[N]]) -> list[list[N]]:
    """The components reached from `roots`, in `successors` order, each
    after every component it reaches (Tarjan). Iterative: a graph can be a
    chain of thousands of nodes."""
    index: dict[N, int] = {}
    low: dict[N, int] = {}
    on_stack: set[N] = set()
    stack: list[N] = []
    out: list[list[N]] = []
    for root in roots:
        if root in index:
            continue
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(successors(root)))]
        while work:
            node, it = work[-1]
            for t in it:
                if t not in index:
                    index[t] = low[t] = len(index)
                    stack.append(t)
                    on_stack.add(t)
                    work.append((t, iter(successors(t))))
                    break
                if t in on_stack:
                    low[node] = min(low[node], index[t])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    component: list[N] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        component.append(w)
                        if w == node:
                            break
                    out.append(component)
    return out
