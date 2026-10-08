# readonly[tuple] param normalization: an all-value-type tuple strips readonly
# (copy semantics), a mixed tuple keeps it and projects readonly only onto the
# reference element (@nocopy proves the element is borrowed, not copied).
from tpy import int32, readonly, nocopy


@nocopy
class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def value_only(p: readonly[tuple[int32, int32]]) -> int32:
    return p[0] + p[1]


def mixed(p: readonly[tuple[int32, Counter]]) -> int32:
    a = p[0]  # tpyc: type(int32)
    b = p[1]  # tpyc: type(/readonly/)
    return a + b.n


# forward: a tuple param of a readonly context (an explicit @readonly method,
# an implicitly readonly dunder) is readonly and still passes on to a
# readonly tuple slot; a mutable slot refuses it per element
# (readonly/error_readonly_method_tuple_param_to_mutable). `+` takes no tuple
# operand, so the dunder is only built, not called.
class Sum:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    @readonly
    def plus(self, o: tuple[int32, Counter]) -> int32:
        return self.k + mixed(o)  # tpyc: ok

    def __add__(self, o: tuple[int32, Counter]) -> int32:
        return self.k + mixed(o)  # tpyc: ok


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# keep: an owning argument takes a copy of the whole readonly tuple, as
# `ys.append(b)` copies a readonly[Box] scalar; a field or subscript store
# refuses it (readonly/error_readonly_tuple_store_copy_hint). The copy itself
# is observed in readonly/readonly_tuple_append_copies (CPython aliases).
def keep(t: readonly[tuple[Box, int32]], xs: list[tuple[Box, int32]]) -> None:
    xs.append(t)  # tpyc: warning(/copies Box into owned storage \(tuple element 0\); use copy\(\)/)


def main() -> None:
    print("value_only:", value_only((3, 4)))
    c = Counter(5)
    print("mixed:", mixed((10, c)))
    s = Sum(100)
    print("forward:", s.plus((1, c)))
    xs: list[tuple[Box, int32]] = []
    keep((Box(7), 8), xs)
    kept = xs[0]
    print("keep:", kept[0].n, kept[1])


main()
