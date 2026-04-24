# Reassigning a StrView local from a safe source to a dangling one must
# still be caught. Covers the provenance-retract path on reassignment now
# that StrView locals participate in provenance tracking.
from tpy import StrView, String

def bad_reassign(p: str) -> StrView:
    sv: StrView = p
    s: String = String("x")
    sv = StrView(s)
    return sv  # tpyc: error(/Cannot return StrView referencing a local or temporary/)
