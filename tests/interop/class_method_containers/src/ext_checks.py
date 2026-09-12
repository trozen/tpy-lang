# Ext-only: behaviors where the compiled extension's method boundary diverges
# from the TPy source (where a container arg is aliased, not copied, and the
# annotations are not enforced). Checked only against the .so.
import array
import class_method_containers as m

s = m.Stats([1])

# Strict-by-kind IN at a method param and at __init__.
try:
    s.scaled((1, 2), 2)
    raise AssertionError("expected TypeError for a non-list method arg")
except TypeError:
    pass
try:
    m.Stats({"a": 1})
    raise AssertionError("expected TypeError for a non-list __init__ arg")
except TypeError:
    pass

# Copy-in at a method param: the owned copy grows, the caller's list doesn't.
caller = [1, 2]
assert s.absorb(caller, 99) == 3
assert caller == [1, 2]

# Copy-out at a borrow-form container return: the result is a fresh object,
# not an alias of the argument (the source would alias).
src = [1, 2]
out = s.same(src)
out.append(3)
assert src == [1, 2]

# Span strictness at a method param: wrong itemsize is a TypeError.
try:
    s.span_sum(array.array("i", [1, 2]))  # int32 buffer into Span[int64]
    raise AssertionError("expected TypeError for an int32 buffer")
except TypeError:
    pass

print("ext-only class-method container checks: PASS")
