# @dynamic protocol type used as generic type argument in record field.
# Verifies codegen emits the resolved type name, not literal "T".
from typing import Protocol
from tpy import Int32, dynamic

@dynamic
class Greeter(Protocol):
    def greet(self) -> str: ...

class Tagged[T]:
    tag: Int32
    def __init__(self, tag: Int32):
        self.tag = tag

class Owner:
    item: Tagged[Greeter]
    def __init__(self, item: Tagged[Greeter]):
        self.item = item

def main() -> None:
    t = Tagged[Greeter](42)
    o = Owner(t)
    print(o.item.tag)

main()
