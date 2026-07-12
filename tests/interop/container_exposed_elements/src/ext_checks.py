# Ext-only: the container copy cliff, extended to exposed-class elements. A
# list[Counter] param is copied in, so mutating an element (bump_all) is NOT
# visible to the caller (where CPython aliases and would show the change).
# Strict-by-type also applies per element.
import container_exposed_elements as m

cs = [m.Counter(1), m.Counter(2)]
m.bump_all(cs)                       # mutates the copied-in list's elements
assert cs[0].value == 1              # caller's elements unchanged (copy-in)
assert cs[1].value == 2

# strict-by-type per element: a bare int is not a Counter
try:
    m.total([m.Counter(1), 5])
    raise AssertionError("expected TypeError for a non-Counter element")
except TypeError:
    pass

# strict-by-type per enum element: a bare int is not a Color member
try:
    m.cycle([1, 2])
    raise AssertionError("expected TypeError for a non-Color element")
except TypeError:
    pass

print("ext-only container-element checks: PASS")
