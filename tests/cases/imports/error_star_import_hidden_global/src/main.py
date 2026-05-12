# Negative guard for the `__all__` filter on typed globals. The
# sibling `error_star_import_not_in_all` covers the same filter for
# functions, but functions worked accidentally before the bind-order
# fix (use sites bypass the namespace kind check). The fix actually
# matters for VARIABLE / ENUM kinds, so this variable-flavored case
# is the relevant regression guard: HIDDEN_VAL exists in lib.py but
# is excluded by `__all__`, so it must not enter scope via
# `from lib import *`.
from lib import *

print(PUBLIC_VAL)
print(HIDDEN_VAL)  # tpyc: error(/HIDDEN_VAL/)
