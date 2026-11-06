# A false reject, pinned (BUGS.md#shared-handle-payload-through-temp-rejected):
# a value record temporary holding a Ptr to the lambda's argument is taken as
# the result's referent, though the result points through the Ptr into `q`
# (CPython prints 7). The module-function twin, `lambda q: unwrap(Wrap(q))`,
# is refused with the same message.
from typing import Callable
from tpy import Ptr, ValueType, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Wrap(ValueType):
    p: Ptr[P]

    def __init__(self, p: P) -> None:
        self.p = p


def unwrap(w: Wrap) -> P:
    return w.p


def main() -> None:
    cw: Callable[[Wrap], P] = unwrap
    # The subject: a call through a Callable value with the Wrap temporary.
    kw: Callable[[P], P] = lambda q: cw(Wrap(q))  # tpyc: error(/Cannot return a borrow of a temporary from this lambda: its result points into 'Wrap\(\.\.\.\)'/)
    w = P(3)
    kw(w).n = 7
    print(w.n)


main()
