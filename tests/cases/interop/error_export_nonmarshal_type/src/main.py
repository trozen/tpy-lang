# A param/return type without a CPython boundary marshaller is rejected when
# the extension glue is generated (only Int64 and int are supported so far).
# tpy: ext_module
from tpy.extern import export


@export
def bad() -> str:  # tpyc: error(/not yet marshallable across the CPython boundary/)
    return "x"
