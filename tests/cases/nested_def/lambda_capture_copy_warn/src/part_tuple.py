# Tuple captures: a tuple local may be the borrow form, so its wording hedges.
from typing import Callable
from tpy import int32
from fixtures import P


# tuple_def: a tuple of records a nested def does not move (a tuple local may
# be the borrow form, so the wording hedges)
def tuple_def() -> Callable[[], int32]:
    t = (P(1), P(2))

    def tget() -> int32:  # tpyc: warning(/nested function 'tget' may keep its own copy of 't'/)
        return t[0].n
    t[0].n = 7
    print("tuple_def:", tget(), t[0].n)
    return tget


# tuple_elem: a tuple local -- an element read off a list, a literal -- may be
# the borrow form, so its wording hedges; an iteration step over the list
# holds the element itself
def tuple_elem(ts: list[tuple[P, P]]) -> None:
    t = ts[0]
    u = (P(3), P(4))
    f: Callable[[], int32] = lambda: t[0].n + u[1].n  # tpyc: warning(/may keep its own copy of 't'/) warning(/may keep its own copy of 'u'/)
    gs: list[Callable[[], int32]] = []
    for v in ts:
        gs.append(lambda: v[1].n)  # tpyc: warning(/copies the object 'v'/) warning(/'v' is bound again/)
    ts[0][0].n = 20
    ts[0][1].n = 30
    u[1].n = 40
    print("tuple_elem:", f(), gs[0]())


# mixed: a tuple of a new record and a borrowed one; only the new one is copied
def mixed() -> None:
    p = P(1)
    t = (P(2), p)
    f: Callable[[], int32] = lambda: t[0].n + t[1].n  # tpyc: warning(/may keep its own copy of 't'/)
    p.n = 10
    t[0].n = 20
    print("mixed:", f())


def main() -> None:
    tget = tuple_def()
    print("tuple_def:", tget())
    tuple_elem([(P(1), P(2))])
    mixed()
