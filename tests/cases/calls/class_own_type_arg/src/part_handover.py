# A nested def capturing a `T` parameter at its last use: at `Own[P]` the
# closure takes the value over (the twin's `[x = std::move(x)]`), at `P` it
# holds the caller's object.
from __future__ import annotations
from typing import Callable
from tpy import Own
from fixtures import P


class Keep[T]:
    def __init__(self) -> None:
        pass

    def get_f(self, x: T) -> Callable[[], Own[T]]:
        # the capture is the parameter's last use
        def inner() -> Own[T]:
            return x  # tpyc: warning(/may copy T into owned storage/)
        return inner


def main() -> None:
    k = Keep[Own[P]]()  # tpyc: ok
    f = k.get_f(P(5))
    print("capture-own", f().n)
    q = P(7)
    g = Keep[P]().get_f(q)
    # the closure holds `q` itself at `P`
    q.n = 8
    print("capture-plain", g().n)
