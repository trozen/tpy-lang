# d.setdefault(k, default) returns a borrow of the stored value, so the
# canonical grouping idiom d.setdefault(k, []).append(x) mutates the dict.
# Value-type values keep copy returns (rebinding the result leaves the
# dict untouched -- CPython-identical for immutable values).
from tpy import Int32


def main():
    groups: dict[str, list[Int32]] = {}
    groups.setdefault("a", []).append(1)
    groups.setdefault("a", []).append(2)
    groups.setdefault("b", []).append(3)
    print(len(groups["a"]), len(groups["b"]))
    print(groups["a"])

    # Binding form: the result aliases the stored value (CPython identity).
    d: dict[str, list[Int32]] = {}
    lst = d.setdefault("k", [])
    lst.append(42)
    print(d["k"])

    counts: dict[str, Int32] = {}
    n = counts.setdefault("hits", 5)
    n = n + 1
    print(n, counts["hits"])


main()
