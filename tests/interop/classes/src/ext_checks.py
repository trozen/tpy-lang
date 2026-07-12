# Ext-only: where the compiled type diverges from the plain Python source class.
# The compiled Counter is a fixed-layout C type -- its fields are typed (a
# wrong-typed set raises), it has no instance __dict__ (no ad-hoc attributes),
# and it is final (not subclassable). The source class allows all three, so
# these are checked only against the built .so, never in cpy-parity.
import classes

c = classes.Counter(1, "x")

try:
    c.value = "not an int"          # typed field setter rejects wrong type
    raise AssertionError("expected TypeError on typed field set")
except TypeError:
    pass

try:
    classes.Counter("not an int", "x")   # wrong-typed __init__ arg
    raise AssertionError("expected TypeError on constructor")
except TypeError:
    pass

try:
    c.extra = 5                     # no instance __dict__
    raise AssertionError("expected AttributeError on ad-hoc attribute")
except AttributeError:
    pass

try:
    class Sub(classes.Counter):     # final type, not subclassable
        pass
    raise AssertionError("expected TypeError on subclassing")
except TypeError:
    pass

print("ext-only class checks: PASS")
