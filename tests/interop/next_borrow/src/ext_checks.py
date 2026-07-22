# Ext-only: the borrow-LIST __next__ COPY divergence (plain Python would
# alias the same stored row; containers have no boundary view path, so
# each next() hands out a fresh copy -- warned at compile).
import next_borrow as m

r = iter(m.Rows())
row = next(r)
row.append(4)
assert next(r) == [1, 2, 3]

print("ext-only next-borrow checks: PASS")
