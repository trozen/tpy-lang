# A scalar type constructor over a TYPE-PARAM-typed argument (`int32(x)` under
# `[T: AnyFixedInt]`): the monomorphized binding renders as the bare name, which
# is what the resolved __init__ overload's template expands over. The generic
# twin of the concrete `int32(n)` spelling, which has always worked.
from tpy import int32, int64, uint32, AnyFixedInt, AnyFixedSigned, AnyFixedUnsigned


# free function: narrowing the type param to a fixed width.
def narrow[T: AnyFixedInt](x: T) -> int32:
    return int32(x)  # tpyc: ok


# free function: widening it.
def widen[T: AnyFixedInt](x: T) -> int64:
    return int64(x)  # tpyc: ok


# free function: a bound that NARROWS AnyFixedInt admits the same conversion --
# the stub declares AnyFixedSigned/AnyFixedUnsigned as sub-protocols of it, the
# way the C++ concepts subsume it.
def narrow_signed[T: AnyFixedSigned](x: T) -> int32:
    return int32(x)  # tpyc: ok


def narrow_unsigned[T: AnyFixedUnsigned](x: T) -> int32:
    return int32(x)  # tpyc: ok


class Counter[T: AnyFixedInt]:
    n: T

    def __init__(self, n: T) -> None:
        self.n = n  # tpyc: warning(/may copy T into field/)

    # method: the same conversion off a T-typed FIELD read.
    def as_int32(self) -> int32:
        return int32(self.n)  # tpyc: ok


def main() -> None:
    print("narrow", narrow(int64(7)), narrow(int32(-2)))
    print("widen", widen(int32(8)))
    print("signed", narrow_signed(int64(-3)))
    print("unsigned", narrow_unsigned(uint32(9)))
    print("method", Counter(int64(9)).as_int32())


main()
