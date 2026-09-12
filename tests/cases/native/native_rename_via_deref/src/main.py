# Method call through a user-defined __deref__ whose target has an @native
# rename. Exercises the deref_chain branch in codegen that was previously
# emitting `expr.method` directly; it must now honor the native rename too.
# tpy: include("native_types.hpp")
from tpy import int32, copy, auto_readonly
from tpy.extern import native

@native("x::Widget")
class Widget:
    id_: int32
    @native("getId")
    def GetId(self) -> int32: ...

class WRef:
    _target: Widget
    def __init__(self, target: Widget) -> None:
        self._target = copy(target)
    @auto_readonly
    def __deref__(self) -> Widget:
        return self._target

def main() -> None:
    w: Widget = Widget(42)
    r = WRef(w)
    # Must emit `r.__deref__().getId()`, not `r.__deref__().GetId()`.
    print(r.GetId())

main()
