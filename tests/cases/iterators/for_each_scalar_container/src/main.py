# THIR container iteration + len(): `for x in <list / dict[fixed-int]>:` (value-scalar
# loop var, the begin/end iterator loop) and `for i in range(len(c)): c[i]` (len as a
# range bound, which lights up the bounds-safe container-subscript branch). The loop
# vars are value scalars (copied), so there is no reference/aliasing distinction here.
from tpy import int32


def total(items: list[int32]) -> int32:
    s = 0
    for x in items:
        s = s + x
    return s


def count_pos(xs: list[int32]) -> int32:
    n = 0
    for i in range(len(xs)):
        if xs[i] > 0:
            n = n + 1
    return n


def keysum(d: dict[int32, int32]) -> int32:
    s = 0
    for k in d:
        s = s + k
    return s


def main() -> None:
    xs = [1, -2, 3, -4, 5]
    print(total(xs))
    print(count_pos(xs))
    print(len(xs))
    scores = {10: 100, 20: 200}
    print(keysum(scores))


main()
