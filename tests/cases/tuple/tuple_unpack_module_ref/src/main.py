# Module-level tuple unpack of REFERENCE elements must alias (CPython parity),
# not copy. Both the bare `a, b = g0, g1` and parenthesized `a, b = (g0, g1)`
# forms are exercised, plus a subscript source -- each followed by mutation
# through the unpacked alias and an observation on the original, so a silent
# copy (the old global-unpack T*->T bug) would diverge from CPython here.
from tpy import int32


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


g0 = Counter(1)
g1 = Counter(2)

# Bare form.
a, b = g0, g1
a.bump()
b.bump()
print(g0.n)  # 2 -- a aliases g0
print(g1.n)  # 3 -- b aliases g1

# Parenthesized form.
c, d = (g0, g1)
c.bump()
print(g0.n)  # 3 -- c aliases g0 (same object as a)

# Subscript source (takes the hidden-temp lowering); alias through the temp.
items = [Counter(10), Counter(20)]
p, q = items[0], items[1]
q.bump()
print(items[1].n)  # 21 -- q aliases items[1]

# Swap reference globals holding DISTINCT values so the exchange is
# observable (equal values would pass under any behavior), then mutate
# through the new g0 binding to confirm it carries the swapped object.
g1.bump()
g1.bump()
print(g0.n)  # 3
print(g1.n)  # 5
g0, g1 = g1, g0
g0.bump()
print(g0.n)  # 6 -- g0 now the former g1 object (5 -> 6)
print(g1.n)  # 3 -- g1 now the former g0 object
