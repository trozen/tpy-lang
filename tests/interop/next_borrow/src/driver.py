# Shared across ext-exec and cpy-parity: values AND class-borrow aliasing
# (every next() of a never-rebound field is the SAME object, write-through
# visible -- plain Python behaves identically, so these are parity-safe).
# Only the borrow-LIST copy divergence stays out of here (ext_checks.py).
import next_borrow as m

print([n.v for n in m.Repeat(7)])
print([row for row in m.Rows()])
print([n.v for n in m.Fresh()])
print(next(iter(m.Repeat(1))).v)

i = iter(m.Repeat(5))
a = next(i)
b = next(i)
print(a is b)
a.v = 99
print(next(i).v)

sub = m.RepeatSub(3)
it = iter(sub)
print(it is sub)
x = next(it)
print(x is next(it))
x.v = 8
print(next(it).v)

p = iter(m.Peek(4))
r1 = next(p)
r2 = next(p)
print(r1 is r2)
r1.v = 6
print(r2.v)
