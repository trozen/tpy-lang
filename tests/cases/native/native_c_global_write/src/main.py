from tpy.extern import native_global
from tpy import Int32

# Write to native C globals at function scope.
# Renamed globals must use the C name in the assignment target.

opentop: Int32 = native_global("opentop", binding="C")
counter: Int32 = native_global("g_counter", binding="C")

def set_same_name(val: Int32) -> None:
    global opentop
    opentop = val

def set_renamed(val: Int32) -> None:
    global counter
    counter = val

def main() -> None:
    set_same_name(Int32(10))
    print(opentop)
    set_renamed(Int32(20))
    print(counter)

main()
