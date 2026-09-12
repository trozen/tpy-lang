# Set algebra: union, intersection, difference, symmetric_difference, predicates
from tpy import int32

def main() -> None:
    a: set[int32] = {1, 2, 3}
    b: set[int32] = {2, 3, 4}

    print(a.union(b))
    print(a.intersection(b))
    print(a.difference(b))
    print(a.symmetric_difference(b))

    # Predicates
    c: set[int32] = {1, 2}
    print(c.issubset(a))
    print(a.issubset(c))
    print(a.issuperset(c))
    print(a.isdisjoint(b))

    d: set[int32] = {10, 20}
    print(a.isdisjoint(d))

    # In-place updates
    e: set[int32] = {1, 2}
    e.update(b)
    print(e)

    f: set[int32] = {1, 2, 3, 4}
    f.intersection_update(a)
    print(f)

    g: set[int32] = {1, 2, 3}
    g.difference_update(b)
    print(g)

    h: set[int32] = {1, 2, 3}
    h.symmetric_difference_update(b)
    print(h)

main()
