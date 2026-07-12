# Shared across the ext-exec and cpy-parity runs: the compiled Vec2 operators
# must behave like a plain Python class with the same dunder bodies,
# including __iadd__ mutating the SAME object (printed as an identity
# comparison, not a raw id() -- ids are host-dependent) rather than rebinding
# to a fresh one.
import class_operators

a = class_operators.Vec2(1, 2)
b = class_operators.Vec2(3, 4)

c = a + b
print(c.x, c.y)                 # 4 6

sub = b - a                     # __sub__: order matters (3-1, 4-2)
print(sub.x, sub.y)             # 2 2
rsub = 10 - a                   # __rsub__: order matters (10-1, 10-2)
print(rsub.x, rsub.y)           # 9 8

d = a * 3
print(d.x, d.y)                 # 3 6
e = 3 * a                       # __rmul__: int * Vec2
print(e.x, e.y)                 # 3 6

f = -a
print(f.x, f.y)                 # -1 -2

g = a ** 2
print(g.x, g.y)                 # 2 4

before = a
a += b                          # __iadd__: mutates in place
print(a is before)              # True -- same object, not a fresh one
print(a.x, a.y)                 # 4 6
