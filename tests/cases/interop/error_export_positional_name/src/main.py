# @export inside an ext_module must be bare -- no positional rename argument.
__tpy__ = {"mod": "ext"}
from tpy import int64
from tpy.extern import export


@export("renamed")  # tpyc: error(/@export must be bare/)
def bad() -> int64:
    return 1
