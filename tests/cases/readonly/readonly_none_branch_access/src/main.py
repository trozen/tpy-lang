# None-seeded variable assigned from a readonly param inside a branch
# should allow read-only access after narrowing with `is not None`.
from tpy import int32, readonly

class Box:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v

    @readonly
    def get_v(self) -> int32:
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

observe_field(True, Box(int32(42)))
observe_field(False, Box(int32(0)))
observe_method(True, Box(int32(99)))
