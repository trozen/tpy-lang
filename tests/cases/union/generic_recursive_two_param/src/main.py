# Two-parameter generic recursive alias with a dict alternative:
# type DictTree[K, V] = V | dict[K, DictTree[K, V]]. Pins multi-param
# substitution + dict-keyed recursion + match dispatch on the dict alternative.
# Generic over both K and V, so the leaf uses case _ (count-style traversal).
from tpy import int32

type DictTree[K, V] = V | dict[K, DictTree[K, V]]


def leaf_count[K, V](t: DictTree[K, V]) -> int32:
    match t:
        case dict() as d:
            acc = 0
            for v in d.values():
                acc += leaf_count(v)
            return acc
        case _:
            return 1


def main() -> None:
    t: DictTree[str, int32] = {"a": 1, "b": {"c": 2, "d": 3}}
    print(leaf_count(t))
    leaf: DictTree[str, int32] = 5
    print(leaf_count(leaf))


main()
