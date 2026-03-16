# Printing dicts nested inside other containers (dict, list, tuple, set).
from tpy import Int32


def main():
    d: dict[str, dict[str, Int32]] = {"a": {"x": 1, "y": 2}, "b": {"z": 3}}
    print(d)

    nested: dict[str, dict[str, dict[str, Int32]]] = {
        "outer": {"mid": {"inner": 42}}
    }
    print(nested)

    with_set: dict[str, set[Int32]] = {"evens": {2, 4}, "odds": {1, 3}}
    print(with_set)

    rows: list[dict[str, Int32]] = [{"a": 1}, {"b": 2}]
    print(rows)

    t: tuple[dict[str, Int32], Int32] = ({"a": 1}, 42)
    print(t)


main()
