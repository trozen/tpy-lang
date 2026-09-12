# A scalar coerce whose source is a FIELD (not a plain name) at an Own[int32]
# slot: field sources are outside the name-only rows, so `take(h.big)` rejects.
# The bare-name source is pinned by tests/cases/pointers/own_coercion.
from tpy import int32, Own


class H:
    big: int

    def __init__(self) -> None:
        self.big = 7


def take(x: Own[int32]) -> int32:
    return x


def field_source(h: H) -> None:
    print(take(h.big))  # tpyc: error(/call\.arg_shape\.own_scalar/)


def main() -> None:
    field_source(H())


main()
