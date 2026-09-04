# `del d[a][b]` -- the receiver is itself a subscript, so the inner read is the
# lvalue the del mutates. Reading the inner container afterwards proves it was
# the STORED one and not a copy.
from tpy import Int32


def drop(d: dict[str, dict[str, Int32]]) -> Int32:
    # The nested del: the inner dict loses the key in place.
    del d["a"]["b"]
    return len(d["a"])


def drop_list(rows: list[dict[str, Int32]]) -> Int32:
    # The same shape with a list receiver for the outer level.
    del rows[0]["x"]
    return len(rows[0])


def main() -> None:
    outer: dict[str, dict[str, Int32]] = {"a": {"b": 1, "c": 2}}
    print(drop(outer))
    print(len(outer["a"]), outer["a"]["c"])
    rows: list[dict[str, Int32]] = [{"x": 7, "y": 8}]
    print(drop_list(rows))
    print(len(rows[0]), rows[0]["y"])


main()
