# Associated-type inference has nothing to unify against when the conformer
# lacks the bound protocol's method: R stays unsolved and the call is rejected
# with the cannot-infer diagnostic (not a crash or a bogus binding).
# NB the message is known-suboptimal here -- the real cause is that NoGet
# doesn't conform to Container[R] (the explicit-args path says so). Tracked as
# a diagnostic-quality item in BUGS.md; this pins current behavior meanwhile.
from typing import Protocol


class Container[R](Protocol):
    def get(self) -> R: ...


class NoGet:
    v: int

    def __init__(self, v: int):
        self.v = v


def unwrap[R, T: Container[R]](x: T) -> R:
    return x.get()


def main() -> None:
    print(unwrap(NoGet(1)))   # tpyc: error(/Cannot infer type arguments for 'unwrap'/)


main()
