# A `match` whose subject is a FIELD holding a recursive-union alias: the
# wrapper hop the dispatch needs is not spelled off a field read. Seating the
# field also draws the ordinary copy-into-field warning.
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


class Holder:
    t: "Tree[int]"

    def __init__(self, t: "Tree[int]") -> None:
        self.t = t


def field_subject(h: Holder) -> Int32:
    # The subject is a field of recursive-union type.
    match h.t:  # tpyc: error(/stmt\.match/)
        case int() as v:
            return v
        case _:
            return 0


def main() -> None:
    print(field_subject(Holder(3)))


main()
