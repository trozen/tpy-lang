# The tpy-native owned `String` is rejected as an @export RETURN type too, not
# just a parameter -- only the Python-facing `str` marshals back to the caller.
# tpy: ext_module
from tpy import String
from tpy.extern import export


@export
def f() -> String:  # tpyc: error(/return of type.*cannot cross the CPython boundary/)
    return String("x")
