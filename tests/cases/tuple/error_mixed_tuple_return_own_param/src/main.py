# Returning an `Own[tuple[A, B]]` param at `tuple[Own[A], B]`: B would dangle.
# docs/LANGUAGE_FEATURES.md "Borrow Form vs Storage Form".
from tpy import Own, int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def ret_param(t: Own[tuple[A, B]]) -> tuple[Own[A], B]:
    # an ownership-transfer parameter's elements are the body's storage
    return t  # tpyc: error(/storage owned by the function/)


def main() -> None:
    r = ret_param((A(1), B(2)))
    print(r[0].v, r[1].v)


main()
