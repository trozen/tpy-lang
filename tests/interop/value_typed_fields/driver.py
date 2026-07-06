# Shared across the ext-exec and cpy-parity runs: reads, whole-value replacement,
# and value equality of an exposed value-type field all match the plain Python
# source class. (Mutation attempts and copy-out identity diverge -- ext-only, in
# ext_checks.py.)
import value_typed_fields as m

b = m.Box(m.Point(1, 2))
print(b.origin.x, b.origin.y)          # 1 2   (copy-out read)
print(b.origin == m.Point(1, 2))       # True  (structural __eq__ crosses)
print(b.origin == m.Point(9, 9))       # False
print(b.get_origin() == m.Point(1, 2)) # True  (field crosses out via a method too)

b.origin = m.Point(3, 4)               # replace the whole value (holder field is r/w)
print(b.origin.x, b.origin.y)          # 3 4
print(b.origin == m.Point(3, 4))       # True

p = m.Point(5, 6)
print(p.x, p.y)                        # 5 6   (standalone read)
print(p == m.Point(5, 6))              # True
