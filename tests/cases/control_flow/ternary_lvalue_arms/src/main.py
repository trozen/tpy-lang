# Ternary arm shapes that keep the C++ `?:` an LVALUE: container-ELEMENT
# subscripts at a record result, at a pointer-Optional result (plain and
# Optional element types), and NAME arms at an Array result. Each selection
# aliases the element it picked, so mutation through the result is visible on
# the container.
from tpy import int32, Array


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def pick_elem(rs: list[Rec], c: bool) -> int32:
    r = rs[0] if c else rs[1]   # tpyc: ok -- two element lvalues
    r.n += 10                   # writes through to the picked element
    return r.n


def pick_opt_elem(rs: list[Rec], c: bool) -> int32:
    # The select takes the element's ADDRESS; main() observes the mutation.
    p = rs[0] if c else None    # tpyc: ok
    if p is not None:
        p.n += 100
        return p.n
    return -1


def pick_optional_element(xs: list[Rec | None], c: bool) -> int32:
    # The element is a pointer-repr Optional, so the select yields the `Rec*`
    # itself.
    q = xs[0] if c else None    # tpyc: ok
    if q is not None:
        q.n += 1000
        return q.n
    return -1


def pick_array(a: Array[int32, 2], b: Array[int32, 2], c: bool) -> int32:
    x = a if c else b           # tpyc: ok -- an Array select aliases
    x[0] = 7
    return a[0]


def main() -> None:
    rs = [Rec(1), Rec(2)]
    print(pick_elem(rs, True), rs[0].n)
    print(pick_opt_elem(rs, True), rs[0].n)
    xs: list[Rec | None] = [Rec(5)]
    print(pick_optional_element(xs, True))
    first = xs[0]
    if first is not None:
        print(first.n)
    arr: Array[int32, 2] = [0, 0]
    brr: Array[int32, 2] = [0, 0]
    print(pick_array(arr, brr, True), arr[0])


main()
