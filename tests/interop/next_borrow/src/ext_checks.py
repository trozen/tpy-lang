# Ext-only: the borrow-form __next__ COPY semantics (plain Python would
# alias -- each next() there is the same object). Distinct objects, real
# data (a garbage payload was the original miscompile), no write-through.
import next_borrow as m

i = iter(m.Repeat(5))
a = next(i)
b = next(i)
assert a is not b
assert (a.v, b.v) == (5, 5), (a.v, b.v)
a.v = 99
assert next(i).v == 5

r = iter(m.Rows())
row = next(r)
row.append(4)
assert next(r) == [1, 2, 3]

print("ext-only next-borrow checks: PASS")
