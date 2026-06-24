# @export inside an ext_module must be bare -- no positional rename argument.
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export("renamed")  # tpyc: error(/@export must be bare/)
def bad() -> Int64:
    return 1
