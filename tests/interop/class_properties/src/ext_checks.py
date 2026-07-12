# Ext-only: where the compiled property exposure diverges from the plain
# Python source class.
import class_properties as m

r = m.Rect(3, 4)

# `_`-named properties are internal payload state -- readable (and, for
# _scale, writable) under plain Python, absent on the .so (the uniform
# `_`-attribute boundary rule).
assert not hasattr(r, "_diag")
assert not hasattr(r, "_scale")
try:
    r._scale = 5
    raise AssertionError("expected AttributeError setting internal _scale")
except AttributeError:
    pass

# The setter is strict-by-type where the plain source property accepts
# anything assignable.
try:
    r.width = "x"
    raise AssertionError("expected TypeError setting width to a str")
except TypeError:
    pass

# ... but the numeric marshaller coerces bool via __index__, so True lands as
# the int 1 (plain Python would store the bool True itself).
r.width = True
assert r.width == 1 and type(r.width) is int
r.width = 3

# Property accessors cross as getset only -- the C++-side setter method name
# never leaks as a callable.
assert not hasattr(r, "set_width")

# The enum setter is strict by member type (a bare int is not a Color).
try:
    r.color = 2
    raise AssertionError("expected TypeError setting color to a bare int")
except TypeError:
    pass

# `del` on a FIELD raises AttributeError on the .so (fixed C++ layout); plain
# Python deletes the instance attribute successfully.
try:
    del r.name
    raise AssertionError("expected AttributeError deleting a field")
except AttributeError:
    pass

print("ext-only property checks: PASS")
