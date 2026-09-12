# Test qualified typing access (typing.Optional, typing.Protocol)
import typing
from tpy import int32

def maybe_add(x: typing.Optional[int32], y: int32) -> int32:
    if x is not None:
        return x + y
    return y

class Printable(typing.Protocol):
    def get_val(self) -> int32: ...

class Wrapper:
    val: int32
    def __init__(self, v: int32):
        self.val = v
    def get_val(self) -> int32:
        return self.val

def show(item: Printable) -> None:
    print(item.get_val())

def main():
    print(maybe_add(int32(3), int32(4)))
    print(maybe_add(None, int32(10)))
    show(Wrapper(int32(42)))

main()
