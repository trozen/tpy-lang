# Moving a string across an extern "C" boundary: `str` has no C spelling, so the
# signature carries Ptr[readonly[UInt8]] and the body converts by hand. Both
# directions are exercised end to end.
from tpy.extern import export
from tpy import Ptr, Int32, UInt8, String, readonly
from tpy.unsafe import unsafe_cstr, unsafe_str_from_cstr

# Stands in for a C sink such as `void tpy_log(const uint8_t *msg)`.
@export(binding="C")
def tpy_log(msg: Ptr[readonly[UInt8]]) -> None:
    print(unsafe_str_from_cstr(msg))

@export(binding="C")
def greet(name: Ptr[readonly[UInt8]]) -> Int32:
    # Inbound: decode the C string into an owned TPy str. The comparison is
    # the subject -- it must compare contents, not the incoming pointer.
    who = unsafe_str_from_cstr(name)
    if who == "world":
        print("greeting the world")
    greeting = String("Hello, " + who + "!")
    # Outbound: unsafe_cstr() borrows `greeting`'s buffer, so the pointer is
    # only good while that String lives.
    tpy_log(unsafe_cstr(greeting))
    return len(who)

def main() -> None:
    arg = String("world")
    print(greet(unsafe_cstr(arg)))

main()
