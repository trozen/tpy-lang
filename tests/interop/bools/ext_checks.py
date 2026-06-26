# Ext-only: behaviors where the compiled extension's boundary coercion diverges
# from the TPy source (where `bool` is just an annotation). identity() returns a
# real coerced bool in the .so but the untouched argument in the source, and a
# __bool__-raising argument errors at the .so boundary (PyObject_IsTrue) while
# the source returns it unexamined. So these are checked only against the .so.
import bools

# Non-bool arguments coerce to a real bool at the boundary.
assert bools.identity(0) is False
assert bools.identity(5) is True
assert bools.identity([]) is False
assert bools.identity([1, 2]) is True

# `and` coerces its operands too: `[] and [1]` is `[]` in the source, False here.
assert bools.both([], [1]) is False

# A __bool__ that raises propagates as that exception from the boundary.
class Boom:
    def __bool__(self):
        raise ValueError("boom")


try:
    bools.identity(Boom())
    raise AssertionError("expected ValueError from the bool boundary")
except ValueError:
    pass

print("ext-only bool checks: PASS")
