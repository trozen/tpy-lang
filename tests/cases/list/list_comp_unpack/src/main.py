# List comprehension: tuple unpacking in generator
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    # Unpack from list of tuples
    pairs: list[tuple[str, int32]] = [("a", 1), ("b", 2), ("c", 3)]
    values = [v for k, v in pairs]
    print(values)

    keys = [k for k, v in pairs]
    print(keys)

    # Unpack from dict.items()
    d: dict[str, int32] = {"x": 10, "y": 20, "z": 30}
    doubled = [v * 2 for k, v in d.items()]
    print(doubled)

    # Unpack with filter
    big_keys = [k for k, v in d.items() if v > 15]
    print(big_keys)

    # Transform both elements
    labels = [k + "=" + str(v) for k, v in d.items()]
    print(labels)

    # Discard with _
    vals_only = [v for _, v in pairs]
    print(vals_only)

    # Filter on first variable
    filtered = [v for k, v in d.items() if k != "x"]
    print(filtered)

    # 3-element tuple unpack
    triples: list[tuple[str, int32, bool]] = [("a", 1, True), ("b", 2, False), ("c", 3, True)]
    middle = [n for _, n, _ in triples]
    print(middle)

    first_and_last = [s + ":" + str(b) for s, _, b in triples]
    print(first_and_last)

    # Non-value element type (const auto& binding via dict.items())
    point_map: dict[str, Point] = {"a": Point(1, 2), "b": Point(3, 4)}
    pts = [p for _, p in point_map.items()]
    print(pts[0].x)
    print(pts[1].y)

main()
