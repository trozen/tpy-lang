# List of tuples with int/float literals: different literal values are compatible
from tpy import int32

def main() -> None:
    # Int literal tuples
    pairs: list[tuple[str, int32]] = [("a", 1), ("b", 2), ("c", 3)]
    print(pairs[0], pairs[1])

    # Float literal tuples
    points: list[tuple[float, float]] = [(1.0, 2.0), (3.0, 4.0)]
    print(points[0])

    # Dict from annotated list of tuples via constructor
    d = dict[str, int32](pairs)
    print(d["a"], d["c"])

    # Unannotated list of tuples (IntLiteralType resolved inside tuples)
    raw = [("x", 10), ("y", 20)]
    # the unpack holder of a value tuple stays a value copy (no member aliases).
    d2 = dict[str, int32]((k, v) for k, v in raw)
    print(d2["x"], d2["y"])

main()
