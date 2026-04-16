# Error: field type pattern doesn't match any variant
from tpy import Int32, Own


class Box[T]:
    value: T
    def __init__(self, value: Own[T]) -> None:
        self.value = value


def bad(x: Box[str] | Box[Int32]) -> str:
    match x:
        case Box(value=float() as v):  # tpyc: error(/no 'Box' variant/)
            return "float"
        case _:
            return "other"


def main() -> None:
    pass

main()
