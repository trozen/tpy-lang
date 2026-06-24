# @export(binding="C") is invalid inside an ext_module: there @export exposes
# to CPython, not extern "C".
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export(binding="C")  # tpyc: error(/exposes to CPython/)
def bad() -> Int64:
    return 1
