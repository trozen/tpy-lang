# A @cpp_template repeating a runtime-value placeholder is rejected --
# textual paste would re-evaluate the argument (side effects run twice).
from tpy.extern import cpp_template
from tpy import Int32

@cpp_template("{0} + {0}")
def dbl(x: Int32) -> Int32: ...  # tpyc: error(/runtime-value placeholder '\{0\}' more than once/)
