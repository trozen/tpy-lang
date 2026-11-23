# A lambda returning its capture through `Callable[[], T]` is admitted
# where the instantiation's result is no borrow (a value, `Own[P]`).
from __future__ import annotations
from typing import Callable
from tpy import Own
from fixtures import P


def mkf[T](x: T) -> Callable[[], T]:
    return lambda: x  # tpyc: warning(/lambda at line 10 may keep its own copy of 'x'/)


class Keep[T]:
    f: Callable[[], T]

    def __init__(self, x: T) -> None:
        self.f = lambda: x  # tpyc: warning(/lambda at line 17 may keep its own copy of 'x'/)


def main() -> None:
    print("lambda-value", mkf(3)())
    # a fresh value: the lambda owns the only copy
    # each call of the lambda copies the value it holds
    k = Keep[Own[P]](P(5))  # tpyc: warning(/copies P into owned storage in 'Keep.__init__' \(part_scopes.py:17\)/)
    print("lambda-class", k.f().n)
