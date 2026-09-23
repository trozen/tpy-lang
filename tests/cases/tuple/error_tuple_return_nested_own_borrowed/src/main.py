# A borrowed source at an Own element NESTED inside a returned tuple literal
# is refused like the direct element: an Own element of a returned literal
# needs an owned value (a borrowed one is an error there, with copy() as the
# remedy), at any depth.
# TO BE FIXED: the scalar twin WARNS and copies here; this error becomes that
# warning (BUGS.md#borrowed-tuple-at-own-call-arg, plan unit U3 D1).
from tpy import Own, int32


class C:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def nested(a: C) -> tuple[int32, tuple[int32, Own[C]]]:
    return (1, (2, a))  # tpyc: error(/Cannot return borrowed value as tuple element 1\.1 Own\[C\]/)


def main() -> None:
    t = nested(C(1))
    print(t[0])


main()
