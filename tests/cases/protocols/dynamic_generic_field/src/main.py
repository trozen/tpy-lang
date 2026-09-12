# @dynamic protocol type used as generic type argument in record field.
# Verifies codegen emits the resolved type name, not literal "T".
# Uses Own[Tagged[Greeter]] to transfer ownership; an implicit copy of
# Tagged[Greeter] would be sema-rejected because Greeter (abstract
# @dynamic) is non-copyable and propagates through the type-args walk
# in is_type_non_copyable (a conservative over-approximation -- Tagged
# is phantom in T -- but the move-based pattern is idiomatic anyway).
from typing import Protocol
from tpy import int32, Own, dynamic

@dynamic
class Greeter(Protocol):
    def greet(self) -> str: ...

class Tagged[T]:
    tag: int32
    def __init__(self, tag: int32):
        self.tag = tag

class Owner:
    item: Tagged[Greeter]
    def __init__(self, item: Own[Tagged[Greeter]]):
        self.item = item

def main() -> None:
    o = Owner(Tagged[Greeter](42))
    print(o.item.tag)

main()
