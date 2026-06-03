# isinstance on a type parameter bounded by a structural protocol is rejected:
# the param could instantiate to a union / Any the static trait can't resolve.
from typing import Protocol

class Named(Protocol):
    def name(self) -> str: ...

class Thing:
    def __init__(self):
        pass
    def name(self) -> str:
        return "t"

def f[T: Named](x: T) -> bool:
    return isinstance(x, Thing)  # tpyc: error(/only non-polymorphic class bounds/)

def main():
    print(f(Thing()))

main()
