# A container that is ALREADY the union's member element (list[V], not a
# concrete list[Int32]) coerces into a recursive-union param -- it is the
# union's list member, so it is NOT falsely rejected by the concrete-container
# diagnostic (which only fires when a leaf genuinely differs from the union).
from tpy import Int32

type V = Int32 | list[V]


def first_kind(v: V) -> str:
    match v:
        case list():
            return "list"
        case _:
            return "scalar"


def main() -> None:
    xs: list[V] = [1, 2, 3]
    print(first_kind(xs))


main()
