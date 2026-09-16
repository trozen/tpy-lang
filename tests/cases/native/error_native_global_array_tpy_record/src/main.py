# The C-ABI gate on native_global(array=True): the array form NAMES its pointee
# as the C element type, so the pointee itself has to be spellable in C -- a
# separate check from the by-value one, which Ptr[T] passes unconditionally.
# The remedy must not be the by-value "pass it as Ptr[T]": the annotation here
# already IS Ptr[T], so that would send the reader in a circle.
from tpy.extern import native, native_global
from tpy import int32, Ptr


@native("Widget", binding="C")
class Widget:
    n: int32


class Gadget:
    def __init__(self, n: int32) -> None:
        self.n = n


# A struct the C header already declares IS spellable as an element type.
widgets: Ptr[Widget] = native_global("widgets", binding="C", array=True)

# A TPy record is not, and the diagnostic names the @native declaration.
gadgets: Ptr[Gadget] = native_global("gadgets", binding="C", array=True)  # tpyc: error(/element type 'Gadget' is not representable in the C ABI; declare the class @native\(binding="C"\) so it names a struct/)
