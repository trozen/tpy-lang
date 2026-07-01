# Ext-only: behaviors where the compiled extension's boundary diverges from the
# TPy source (where the container annotations are not enforced and a container
# arg is aliased, not copied). All of these are checked only against the .so.
import containers as m

# Strict-by-type IN: a non-list / non-dict / non-set arg is a TypeError, where
# the source ignores the annotation and would accept any iterable.
for bad in (42, "abc", (1, 2)):
    try:
        m.sum_list(bad)
        raise AssertionError("expected TypeError for a non-list arg")
    except TypeError:
        pass
try:
    m.dict_sum([("a", 1)])
    raise AssertionError("expected TypeError for a non-dict arg")
except TypeError:
    pass
try:
    m.set_size([1, 2, 3])
    raise AssertionError("expected TypeError for a non-set arg")
except TypeError:
    pass

# tuple arity is enforced at the boundary; the source ignores the annotation.
try:
    m.swap((1, "a", "extra"))
    raise AssertionError("expected TypeError for the wrong tuple arity")
except TypeError:
    pass

# Per-element scalar coercion: list[int] coerces each element via __index__
# (True -> 1), exactly like a scalar int param does at the boundary.
assert m.sum_list([1, True, 2]) == 4

# A non-coercible element raises TypeError from the mid-iteration from_py; the
# source (annotation unenforced) would instead fail later or not at all.
try:
    m.sum_list([1, "two", 3])
    raise AssertionError("expected TypeError for a non-int list element")
except TypeError:
    pass

# Copy-in: a mutation of a container param is NOT visible to the caller (the
# boundary marshalled an owned copy). The source aliases, so this is ext-only.
caller = [1, 2, 3]
assert m.append_to(caller, 99) == 4   # the owned copy grew to length 4 ...
assert caller == [1, 2, 3]            # ... but the caller's list is untouched

print("ext-only container checks: PASS")
