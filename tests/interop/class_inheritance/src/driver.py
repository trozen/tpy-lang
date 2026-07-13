# Shared across ext-exec and cpy-parity: the exposed hierarchy must behave
# like the plain source classes -- real MRO, inherited fields/methods,
# write-through base-typed params, cross-hierarchy equality. Shapes that
# diverge (static dispatch through a base-typed param, hash of a class with
# an own comparison dunder, a typed __lt__ body fed the base) are documented
# and asserted ext-only in ext_checks.py.
import class_inheritance as m

c = m.Circle("c", 2.0)
print(isinstance(c, m.Shape), issubclass(m.Circle, m.Shape))
print([t.__name__ for t in type(c).__mro__])
print(c.name, c.radius)                # inherited getset + own getset
print(c.describe(), c.area())          # MRO override + own method
c.name = "c2"                          # write the inherited field
print(c.name, c.describe())

s = m.Shape("s")
print(s.describe())

m.rename(c, "big")                     # borrow through a base-typed param
print(c.name)                          # write-through on the same object

d = m.Disc("d", 1.0)                   # __init__ inherited from Circle
print(isinstance(d, m.Circle), isinstance(d, m.Shape))
print(d.spin(), d.describe(), d.name, d.radius)
print(c.label, d.label)                # @property inherited from Shape

b = m.LeafBox(width=4)                 # keyword call, 2-level inherited ctor
print(b.w(), b.width, b.tag(), b.tag2())

print(c == m.Circle("big", 9.0))       # inherited __eq__ (name only)
print(c == s, s == c)                  # cross-hierarchy compares
print(m.Circle("a", 1.0) < m.Circle("b", 2.0))
print(m.Circle("b", 2.0) < m.Circle("a", 1.0))
print(hash(s) == len("s"))             # Shape's own __hash__

try:
    s < c                              # Shape has no ordering; Circle's
    print("lt worked")                 # reflected __gt__ is absent too
except TypeError:
    print("no ordering vs base")
