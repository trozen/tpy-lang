from tpy.extern import native_c_global
from tpy import Int32

# Write to native_c_global at function scope.
# Renamed globals must use the C name in the assignment target.

opentop: Int32 = native_c_global("opentop")
counter: Int32 = native_c_global("g_counter")

def set_same_name(val: Int32) -> None:
    global opentop
    opentop = val

def set_renamed(val: Int32) -> None:
    global counter
    counter = val

set_same_name(Int32(10))
set_renamed(Int32(20))
