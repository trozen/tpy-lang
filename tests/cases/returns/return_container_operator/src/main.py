# A container OPERATOR rvalue at an owning-container return: the builtin
# operator builds a fresh container by value, so the return takes its render
# bare.
from tpy import Own, int32


def cat(a: list[int32], b: list[int32]) -> Own[list[int32]]:
    return a + b  # the concat is a fresh list, not an alias of either operand


def uni(a: set[int32], b: set[int32]) -> Own[set[int32]]:
    return a | b


def inter(a: set[int32], b: set[int32]) -> Own[set[int32]]:
    return a & b


def diff(a: set[int32], b: set[int32]) -> Own[set[int32]]:
    return a - b


def sym(a: set[int32], b: set[int32]) -> Own[set[int32]]:
    return a ^ b


def main():
    xs = [1, 2]
    ys = [3]
    got = cat(xs, ys)
    # Mutating the result must not reach either operand -- a returned alias
    # would show up as a length change on xs.
    got.append(9)
    print(len(got), len(xs), len(ys))
    s1 = {1, 2}
    s2 = {2, 3}
    u = uni(s1, s2)
    u.add(9)
    print(len(u), len(s1), len(s2))
    print(len(inter(s1, s2)), len(diff(s1, s2)), len(sym(s1, s2)))


main()
