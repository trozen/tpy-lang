# An exposed class imported from another ext_module cannot cross as a container
# element either: its CPython type handle lives in the defining module's glue,
# so the per-element marshaller here has nothing to reference. Located error, not
# a misbuild -- mirrors the top-level cross-module exposed-type guard.
# tpy: ext_module
from tpy import int64
from tpy.extern import export
from other_mod import Counter


@export
def total(cs: list[Counter]) -> int64:  # tpyc: error(/exposed-class container element from another module/)
    t: int64 = 0
    for c in cs:
        t += c.value
    return t
