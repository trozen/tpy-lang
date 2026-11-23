# tplib classes at `Own[P]`: `ArrayList`'s `a[i]` is an owned copy, as its
# twin's `-> Own[P]`; `Rc[Own[P]]`'s `get` copies the shared payload.
from __future__ import annotations
from tpy import Own
from tplib.array_list import ArrayList
from tplib.rc import Rc
from fixtures import P


def array_list() -> None:
    a = ArrayList[Own[P], 4]()  # tpyc: warning(/copies P into owned storage in 'ArrayList.__getitem__'/)
    q = P(1)
    a.append(q)  # tpyc: warning(/copies P into owned storage/)
    q.n = 10
    g = a.__getitem__(0)
    g.n = 77
    # iteration lends the stored elements
    for p in a:
        p.n += 100
    print("al", a.__getitem__(0).n, g.n, q.n)


def rc() -> None:
    r = Rc[Own[P]].new(P(3))  # tpyc: warning(/copies P into owned storage in 'Rc.get'/)
    s = r.clone()
    g = r.get()
    g.n = 9
    print("rc", r.get().n, s.get().n, g.n)


def main() -> None:
    array_list()
    rc()
