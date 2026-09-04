# The deref+move container return over a reassigned local whose FIRST binding
# is a container-returning CALL: the two-slot rebind machinery, the same one a
# container LITERAL first binding takes.
from tpy import Own, Int32, copy, nocopy


def gen(n: Int32) -> Own[list[Int32]]:
    out: list[Int32] = []
    for i in range(n):
        out.append(i)
    return out


def longest(n: Int32) -> Own[list[Int32]]:
    best = gen(0)  # tpyc: ok
    for i in range(n):
        cur = gen(i)
        if len(cur) > len(best):
            best = copy(cur)
    return best


def main():
    got = longest(4)
    # The returned container is the caller's own storage: the append after the
    # boundary must land on it, not on a copy the callee still owns.
    got.append(99)
    print(len(got), got[-1])


main()
