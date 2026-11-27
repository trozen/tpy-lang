# A protocol dunder with an extra parameter still conforms (its concept is
# unevaluated); the extra operand runs in a direct call.
from typing import Protocol
from tpy import int32
from fixtures import mark_i


class Getter(Protocol):
    def __getitem__(self, k: int32, extra: int32 = 1) -> int32: ...


class Impl:
    def __init__(self) -> None:
        pass

    def __getitem__(self, k: int32, extra: int32 = 1) -> int32:
        return k * 10 + extra


def subscript(x: Getter) -> int32:
    # Impl conforms to Getter; a subscript passes only the key
    return x[4]  # tpyc: ok


def main() -> None:
    i = Impl()
    # bound first: print writes its earlier arguments before evaluating a
    # later one (BUGS.md#print-partial-output-on-raise)
    g = i.__getitem__(3, mark_i("direct getitem", 5))  # tpyc: ok
    print("direct getitem", g)
    print("proto subscript", subscript(i))
