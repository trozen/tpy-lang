# The tpy-native owned `String` is NOT marshallable across the @export
# boundary -- only the Python-facing `str` is (a Python caller supplies str).
# tpy: ext_module
from tpy import String
from tpy.extern import export


@export
def f(x: String) -> int:  # tpyc: error(/parameter 'x'.*not yet marshallable/)
    return 0
