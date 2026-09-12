# for-each over a list of RECORDS: the loop var is a borrow ALIAS of each element
# (const auto& read-only / auto&& when mutated), never a copy -- so mutating a field
# through it is visible in the list (reference semantics, matching CPython). A @nocopy
# element proves no silent copy at the binding (a copy would not compile).
from tpy import int32, nocopy


class P:
    x: int32
    def __init__(self, x: int32):
        self.x = x


@nocopy
class Counter:
    n: int32
    def __init__(self, n: int32):
        self.n = n


def total(ps: list[P]) -> int32:
    s = 0
    for p in ps:          # read-only loop var -> const auto&
        s = s + p.x
    return s


def bump_all(ps: list[P]) -> None:
    for p in ps:          # mutated loop var -> auto&&; the write goes through the alias
        p.x = p.x + 10


def count_total(cs: list[Counter]) -> int32:
    s = 0
    for c in cs:          # @nocopy element: a copy at the binding would be a compile error
        s = s + c.n
    return s


def main() -> None:
    ps = [P(1), P(2), P(3)]
    print(total(ps))       # 6
    bump_all(ps)
    print(total(ps))       # 36 -- mutation through the loop var is visible in the list

    cs = [Counter(5), Counter(7)]
    print(count_total(cs))  # 12


main()
