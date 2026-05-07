# 3-module variable re-export chain: c defines LIMIT, b imports it,
# main imports it from b. Phase 4's _flatten_var_reexport ensures
# the consumer's `using` emission lands at c::LIMIT directly, not
# through b's namespace.
from b import LIMIT

print(LIMIT)
