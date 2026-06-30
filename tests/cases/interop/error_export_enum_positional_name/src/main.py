# @export on an enum inside an ext_module must be bare -- no positional rename
# argument (mirrors the function/class rule).
# tpy: ext_module
from enum import IntEnum
from tpy.extern import export


@export("renamed")  # tpyc: error(/@export must be bare/)
class Color(IntEnum):
    RED = 1
    GREEN = 2
