# Method call through a user-defined __deref__ whose target has an @native
# rename. Exercises the deref_chain branch in codegen that was previously
# emitting `expr.method` directly; it must now honor the native rename too.
__tpy__ = {"includes": ["native_types.hpp"]}
from tpy import int32, copy, auto_readonly
from tpy.extern import native

class A:
    def __init__(self, n: int32) -> None:
        self.n = n

type Tree = int32 | A | list[Tree]

@native("x::Widget")
class Widget:
    id_: int32
    @native("getId")
    def GetId(self) -> int32: ...
    @native("echo")
    def Echo(self, t: Tree) -> int32: ...

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
    # A recursive-union member at the target's method parameter, through
    # the Deref chain: built into the wrapper as at the direct call.
    a = A(1)
    print(r.Echo(a), r.Echo(A(2)))  # tpyc: ok

main()
