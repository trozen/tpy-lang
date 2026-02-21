# None-seeded variable assigned from a readonly param inside a branch
# should allow read-only access after narrowing with `is not None`.
from tpy import Int32, readonly

class Box:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v

    @readonly
    def get_v(self) -> Int32:
        return self.v

@readonly
def observe_field(flag: bool, p: Box) -> None:
    x = None
    if flag:
        x = p
    if x is not None:
        print(x.v)

@readonly
def observe_method(flag: bool, p: Box) -> None:
    x = None
    if flag:
        x = p
    if x is not None:
        print(x.get_v())

observe_field(True, Box(Int32(42)))
observe_field(False, Box(Int32(0)))
observe_method(True, Box(Int32(99)))
