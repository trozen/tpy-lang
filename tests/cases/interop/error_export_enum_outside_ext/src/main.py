# @export on an enum is only meaningful inside a `"mod": "ext"` module, where it
# recreates the enum as a CPython type. Outside one there is no host module to
# attach it to, so it is a parse error (mirrors @export on a class/function).
from enum import IntEnum
from tpy.extern import export


@export  # tpyc: error(/@export is only valid inside an ext module/)
class Color(IntEnum):
    RED = 1
    GREEN = 2
