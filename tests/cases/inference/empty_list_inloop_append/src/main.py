# An empty-list local whose element evidence comes only from an in-loop
# .append() must resolve at post-loop use sites (loop_scope reverts the in-loop
# binding, so the pending list reaches the use site as PendingList[Unknown]).
from tpy import Int32, Int64, Own


def collect_for(xs: list[Int32]) -> Own[list[Int32]]:
    out = []
    for x in xs:
        out.append(x)
    return out


def widen_collect(xs: list[Int32]) -> Own[list[Int64]]:
    # In-loop Int32 element must still widen to the declared Int64 -- the
    # canonical-element use-site check allows coercion, not just exact match.
    out = []
    for x in xs:
        out.append(x)
    return out


def collect_while(n: Int32) -> Own[list[Int32]]:
    out = []
    i = 0
    while i < n:
        out.append(i)
        i += 1
    return out


def collect_cond(xs: list[Int32]) -> Own[list[Int32]]:
    out = []
    for x in xs:
        if x % 2 == 0:
            out.append(x)  # sole evidence is a conditional append in the loop
    return out


def generic_collect[T](xs: list[T]) -> Own[list[T]]:
    out = []
    for x in xs:
        out.append(x)
    return out


def total(ys: list[Int32]) -> Int32:
    return len(ys)


class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def collect_refs(src: list[Counter]) -> Own[list[Counter]]:
    # Reference-type element: appending into a fresh accumulator copies each
    # element (intended here); the returned list owns mutable copies.
    out = []
    for c in src:
        out.append(c)
    return out


def main() -> None:
    print(len(collect_for([1, 2, 3])))
    print(len(widen_collect([1, 2, 3])))
    print(len(collect_while(4)))
    print(len(collect_cond([1, 2, 3, 4])))
    print(len(generic_collect([5, 6])))
    # in-loop-only pinned list reaching a typed param rather than a return
    acc = []
    for v in [10, 20, 30]:
        acc.append(v)
    print(total(acc))
    # the reference-type element is materialized and mutable in the result
    got = collect_refs([Counter(1), Counter(2)])
    got[0].n = 99
    print(got[0].n)


main()
