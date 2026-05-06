# Cycle peer that imports an @overload-grouped function from `b` and
# uses both overloads at distinct call sites. `a` is alphabetically
# earlier than `b`, so a's `bind_imports` runs while b's exports are
# still skeletal. The pre-populated FunctionInfo placeholder for `g`
# in `b.exports.functions` has empty params and VOID return; if
# `register_overload_group` doesn't adopt that skeleton when b later
# finalizes, a's analyzer registry holds the stale empty FI and
# overload resolution at a's call sites fails (or picks wrong target).
from b import g
from tpy import Int32

def use_g_int(n: Int32) -> Int32:
    return g(n)

def use_g_str(s: str) -> Int32:
    return g(s)
