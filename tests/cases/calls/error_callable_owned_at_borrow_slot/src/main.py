# A function returning Own[P] cannot stand at an Fn slot whose bare `P` return
# is a borrow (it reads as a def's `-> P`); the fix spells the slot Own[P].
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the `Callable` -> `Fn`
# fit ("at a slot whose return is a bare reference type -- a borrow
# -- only a callable that hands back a borrow fits").
from tpy import Fn, Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk(p: P) -> Own[P]:
    return P(p.n + 1)


def via(f: Fn[[P], P], x: P) -> None:
    r = f(x)
    r.n += 100


def main() -> None:
    p = P(1)
    via(mk, p)  # tpyc: error(/'mk' cannot be passed as Fn\[\[P\], P\]: it returns Own\[P\], but Fn\[\[P\], P\] returns a borrow; declare Fn\[\[P\], Own\[P\]\]/)


main()
