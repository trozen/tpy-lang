# Printing containers with union/optional element types
from tpy import Int32

class Pt:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y
    def __repr__(self) -> str:
        return "Pt(" + str(self.x) + ", " + str(self.y) + ")"

def main() -> None:
    # Dict with union values
    d: dict[str, Int32 | str] = {"a": 1, "b": "hello"}
    print(d)

    # List with union elements
    lst: list[Int32 | str] = [1, "two", 3]
    print(lst)

    # Dict with optional values
    d2: dict[str, Int32 | None] = {"x": 42, "y": None}
    print(d2)

    # List with optional elements
    l2: list[str | None] = ["a", None, "b"]
    print(l2)

    # Tuple with union members
    t: tuple[Int32 | str, Int32 | str] = (1, "hi")
    print(t)

    # Nested: list in union value
    d3: dict[str, list[int] | str] = {"nums": [1, 2], "tag": "ok"}
    print(d3)

    # Optional list elements
    l3: list[list[int] | None] = [[1, 2], None, [3]]
    print(l3)

    # User records in union
    d4: dict[str, Pt | str] = {"p": Pt(1, 2), "name": "origin"}
    print(d4)

    # List of tuples with union
    l4: list[tuple[str, Int32 | str]] = [("a", 1), ("b", "two")]
    print(l4)

    # Three-way union with None (exercises std::monostate path)
    l5: list[Int32 | str | None] = [1, "two", None]
    print(l5)

main()
