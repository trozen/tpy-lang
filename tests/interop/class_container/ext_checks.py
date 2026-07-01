# Ext-only: item-deletion is rejected (Box defines __setitem__ but not
# __delitem__ -- a TypeError, matching what a plain Python class with the
# same shape would ALSO raise, but the exact message text is not
# byte-matched here since the compiled wrapper's wording is our own, not
# CPython's auto-generated one). A wrong-typed key/value is intercepted by
# the marshaller BEFORE __getitem__/__setitem__'s body runs (the C++
# signature requires the declared Int32/Int64), so it raises a clean
# TypeError -- the plain Python source has no such guard and would instead
# either misbehave silently (a string key never equals an int, so
# __getitem__'s body falls through to the last branch) or raise from deep
# inside the body; this is TPy's static-typing divergence surfacing
# cleanly, not a new one.
import class_container

b = class_container.Box(1, 2, 3)

try:
    del b[0]
    raise AssertionError("expected TypeError on item deletion")
except TypeError:
    pass

try:
    b["not an int"]
    raise AssertionError("expected TypeError on wrong-typed key")
except TypeError:
    pass

try:
    b[0] = "not an int"
    raise AssertionError("expected TypeError on wrong-typed value")
except TypeError:
    pass

print("ext-only container checks: PASS")
