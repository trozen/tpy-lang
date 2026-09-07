# A membership receiver that is a field over a container SUBSCRIPT is not a
# receiver the gate admits, so `";" in h.items[0].name` is rejected today.
from tpy import String, StrView


class Row:
    name: str
    view: StrView
    owned: String

    def __init__(self, n: str) -> None:
        self.name = n
        self.view = "sv"
        self.owned = String(n)


class Holder:
    items: list[Row]

    def __init__(self) -> None:
        self.items = [Row("a;b")]


def has_sep(h: Holder) -> bool:
    return ";" in h.items[0].name  # tpyc: error(/binop.shape.in/)


def main() -> None:
    print(has_sep(Holder()))


main()
