# A param/return type without a CPython boundary marshaller (StrView here -- a
# borrow form, not marshallable across the boundary) is rejected before codegen,
# not during glue generation.
# tpy: ext_module
from tpy import StrView
from tpy.extern import export


@export
def bad() -> StrView:  # tpyc: error(/cannot cross the CPython boundary/)
    return "x"
