# Rebinding a StrView local to a dangling source inside a loop body must
# still be caught after loop exit. Loop iterator provenance now participates
# in the merge, but dangling inside the body still demotes the local on
# exit via the FlowFacts intersect.
from tpy import StrView, String

def bad(items: list[str]) -> StrView:
    sv: StrView = items[0]
    for item in items:
        s: String = String(item)
        sv = StrView(s)
    return sv  # tpyc: error(/Cannot return StrView referencing a local or temporary/)
