from tpy.extern import native_global
from tpy import int32

# Write to native C globals at function scope.
# Renamed globals must use the C name in the assignment target.

opentop: int32 = native_global("opentop", binding="C")
counter: int32 = native_global("g_counter", binding="C")

def set_same_name(val: int32) -> None:
    global opentop
    opentop = val

def set_renamed(val: int32) -> None:
    global counter
    counter = val

def main() -> None:
    set_same_name(int32(10))
    print(opentop)
    set_renamed(int32(20))
    print(counter)

main()
