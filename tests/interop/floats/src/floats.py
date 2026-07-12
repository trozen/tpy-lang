# tpy: ext_module
# CPython extension over the float boundary: float params/returns marshal as
# C++ double (PyFloat_AsDouble/FromDouble). int/bool args coerce via __float__.
# For the values exercised here -- real numbers plus the two error cases
# (non-number, int-too-large-for-double) -- the compiled extension and the TPy
# source under CPython agree, so they are parity-checked in driver.py rather
# than an ext-only file. The boundary coerces per Python's numeric protocols,
# so for exotic args (complex, a bare __float__/__index__ object) it is
# wider/stricter than the untyped source -- a known model property (see
# LANGUAGE_FEATURES); those are deliberately kept out of the parity driver.
from tpy.extern import export


@export
def scale(x: float) -> float:
    return x * 2.5


@export
def addf(a: float, b: float) -> float:
    return a + b
