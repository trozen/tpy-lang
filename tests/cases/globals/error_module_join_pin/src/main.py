# Refuses valid Python by the rule "Numeric widening across reassignments"
# (docs/LANGUAGE_FEATURES.md): a module variable read where its type is
# needed on the spot (a list element) before a wider store is refused naming
# that read, as a function local is: the read was compiled at the narrower type.
from tpy import int64

n = 0
xs = [n]
n = int64(5000000000)  # tpyc: error(/'n' was used as int32 at line 8 \(a list element\).*annotate its first binding: n: int64 = /)
print(xs, n)
