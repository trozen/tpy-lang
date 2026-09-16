# The rejection the C-ABI gate exists for: a @native(binding="C") struct passed
# BY VALUE emits a C++ reference, and since extern "C" does not mangle, the call
# links cleanly while the callee reads a struct where a pointer was passed. The
# remedy has to name the pointer form rather than a bare "use Ptr[T]".
from tpy.extern import native, export
from tpy import int32, Ptr

@native("Widget", binding="C")
class Widget:
    n: int32

# The supported spelling, kept next to the rejected one for contrast.
@export(binding="C")
def value_of(w: Ptr[Widget]) -> int32:
    return w.n

@export(binding="C")
def take(w: Widget) -> int32:  # tpyc: error(/parameter 'w': type 'Widget' is not representable in the C ABI; pass it as Ptr\[T\] -- a struct crosses a C boundary by pointer/)
    return w.n
