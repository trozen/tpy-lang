# Returning an alias of an `Own[tuple]` param at `tuple[Own[A], B]`: B dangles.
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


def relay(p: Own[tuple[A, B]]) -> tuple[Own[A], B]:
    t = p
    # the alias's root is the parameter the body owns
    return t  # tpyc: error(/storage owned by the function/)


def main() -> None:
    r = relay((A(1), B(2)))
    print(r[0].v, r[1].v)


main()
