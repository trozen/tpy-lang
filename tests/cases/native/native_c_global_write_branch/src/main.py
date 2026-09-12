from tpy.extern import native_global
from tpy import int32

# Write to native C globals inside if/else branches (no prior function-scope assignment).
# Must NOT emit a local declaration that shadows the extern global.

opentop: int32 = native_global("opentop", binding="C")
counter: int32 = native_global("g_counter", binding="C")

def update_same_name(a: int32, b: int32) -> None:
    global opentop
    if a < b:
        opentop = a
    else:
        opentop = b

def update_renamed(a: int32, b: int32) -> None:
    global counter
    if a < b:
        counter = a
    else:
        counter = b

def main() -> None:
    update_same_name(int32(10), int32(20))
    print(opentop)
    update_renamed(int32(30), int32(40))
    print(counter)

main()
