# Test qualified typing access (typing.Optional, typing.Protocol)
import typing
from tpy import Int32

def maybe_add(x: typing.Optional[Int32], y: Int32) -> Int32:
    if x is not None:
        return x + y
    return y

class Printable(typing.Protocol):
    def get_val(self) -> Int32: ...

class Wrapper:
    val: Int32
    def __init__(self, v: Int32):
        self.val = v
    def get_val(self) -> Int32:
        return self.val

def show(item: Printable) -> None:
    print(item.get_val())

def main():
    print(maybe_add(Int32(3), Int32(4)))
    print(maybe_add(None, Int32(10)))
    show(Wrapper(Int32(42)))

main()
