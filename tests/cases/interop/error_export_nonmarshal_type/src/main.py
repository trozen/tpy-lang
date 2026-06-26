# A param/return type without a CPython boundary marshaller (str here) is
# rejected before codegen, not during glue generation.
# tpy: ext_module
from tpy.extern import export


@export
def bad() -> str:  # tpyc: error(/not yet marshallable across the CPython boundary/)
    return "x"
