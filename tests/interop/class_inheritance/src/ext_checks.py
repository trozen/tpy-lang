# Ext-only: where the compiled hierarchy diverges from the plain source
# classes (each documented in docs/CPYTHON_INTEROP.md).
import class_inheritance as m

c = m.Circle("c", 2.0)
s = m.Shape("s")

# TPy dispatch is static inside the module: a base-typed param calls the BASE
# method even on a derived payload (plain Python would dispatch to the
# override; the compiler warns at the hiding site -- @dynamic is the hatch).
assert m.describe_via_base(c) == "shape c"

# A class defining any comparison dunder becomes unhashable once exposed
# (PyType_Ready nulls the hash whenever tp_richcompare is populated); plain
# Python unsets hash only for __eq__, so Circle -- defining only __lt__ --
# stays hashable in source form (inheriting Shape's hash) but not on the .so.
try:
    hash(c)
    raise AssertionError("expected TypeError hashing Circle")
except TypeError:
    pass

# `c < s` in plain Python runs Circle.__lt__ and dies on the missing radius
# (AttributeError); the compiled dispatcher's operand guard rejects the
# base-typed operand up front (NotImplemented), degrading the whole compare
# to TypeError. Type the operand as the base to compare across the hierarchy.
try:
    c < s
    raise AssertionError("expected TypeError on mixed-type <")
except TypeError:
    pass

# Shape/Circle carry BASETYPE (each is another exposed class's tp_base),
# which legalizes a Python-side class statement; instantiating the subclass
# is rejected by the tp_init exact-type guard.
class Mine(m.Shape):
    pass

try:
    Mine("m")
    raise AssertionError("expected TypeError instantiating a Python subclass")
except TypeError:
    pass

# A leaf exposed class stays final: the class statement itself is rejected.
try:
    class Leaf(m.Disc):
        pass
    raise AssertionError("expected TypeError subclassing a leaf")
except TypeError:
    pass

print("ext-only inheritance checks: PASS")
