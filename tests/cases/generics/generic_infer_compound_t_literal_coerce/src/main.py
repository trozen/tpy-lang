"""
Generic T inferred as a compound (tuple/list/dict/set): literal-typed elements
in a later arg coerce to the matching slot type of the already-determined T.
Mirrors the scalar `heappush(xs: list[int32], 3)` coercion one structural
layer deeper. Covers int and float literals; the nested-list-of-tuple case
also guards against compound-walk regressions in `resolve_int_literals`.
"""
from heapq import heappush
from tpy import int32, float64


def push_t[T](xs: list[T], item: T) -> None:
    xs.append(item)


def put_dict[K, V](d: dict[K, V], k: K, v: V) -> None:
    d[k] = v


def add_to_set[T](s: set[T], v: T) -> None:
    s.add(v)


def take_any[T](x: T) -> None:
    pass


def pair_any[T](a: T, b: T) -> None:
    pass


def main() -> None:
    # Tuple-shaped T: bare literal `3` coerces to int32 in the second-arg
    # tuple even though T was determined by the first arg.
    pq: list[tuple[int32, str]] = []
    heappush(pq, (3, "third"))  # tpyc: ok
    heappush(pq, (1, "first"))  # tpyc: ok
    push_t(pq, (2, "second"))   # tpyc: ok
    print(len(pq))

    # Float-literal slot: same path, FloatLiteralType -> float64.
    weighted: list[tuple[float64, str]] = []
    push_t(weighted, (1.5, "a"))  # tpyc: ok
    push_t(weighted, (2.5, "b"))  # tpyc: ok
    print(len(weighted))

    # Nested tuple-of-tuple slot.
    nested: list[tuple[tuple[int32, int32], str]] = []
    push_t(nested, ((1, 2), "x"))  # tpyc: ok
    print(len(nested))

    # Dict K + V both fixed-int from separate args; literals coerce in place.
    counts: dict[int32, int32] = {}
    put_dict(counts, 7, 42)  # tpyc: ok
    print(counts[7])

    # set[T] inference with bare literal element.
    seen: set[int32] = set()
    add_to_set(seen, 11)  # tpyc: ok
    add_to_set(seen, 22)  # tpyc: ok
    print(len(seen))

    # T pinned from a list-literal arg whose elements are literal-typed tuples.
    # Exercises resolve_int_literals' compound walk: without map_inner_types
    # recursion, T would reach codegen as list[tuple[IntLiteral, str]] and
    # leak the literal value into the C++ template argument.
    take_any([(1, "a"), (2, "b")])  # tpyc: ok

    # Both args are bare literal tuples (T inferred from first, consistency
    # check + resolution applied through compound shape against the second).
    # Without the unification, codegen leaked `std::tuple<3, std::string>`.
    pair_any((3, "x"), (4, "y"))  # tpyc: ok


main()
