# Test qualified dataclasses.field() form
import dataclasses
from tpy import int32

@dataclasses.dataclass
class Foo:
    items: list[int32] = dataclasses.field(default_factory=list)
    x: int32 = dataclasses.field(default=42)

def main() -> None:
    f = Foo(x=int32(1))
    print(f.items)
    print(f.x)

    f2 = Foo([int32(10), int32(20)], int32(5))
    print(f2.items)
    print(f2.x)

main()
