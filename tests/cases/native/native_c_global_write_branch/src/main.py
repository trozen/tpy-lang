from tpy.extern import native_c_global
from tpy import Int32

# Write to native_c_global inside if/else branches (no prior function-scope assignment).
# Must NOT emit a local declaration that shadows the extern global.

opentop: Int32 = native_c_global("opentop")
counter: Int32 = native_c_global("g_counter")

def update_same_name(a: Int32, b: Int32) -> None:
    global opentop
    if a < b:
        opentop = a
    else:
        opentop = b

def update_renamed(a: Int32, b: Int32) -> None:
    global counter
    if a < b:
        counter = a
    else:
        counter = b

def main() -> None:
    update_same_name(Int32(10), Int32(20))
    print(opentop)
    update_renamed(Int32(30), Int32(40))
    print(counter)

main()
