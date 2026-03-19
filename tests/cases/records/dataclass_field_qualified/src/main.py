# Test qualified dataclasses.field() form
import dataclasses
from tpy import Int32

@dataclasses.dataclass
class Foo:
    items: list[Int32] = dataclasses.field(default_factory=list)
    x: Int32 = dataclasses.field(default=42)

def main() -> None:
    f = Foo(x=Int32(1))
    print(f.items)
    print(f.x)

    f2 = Foo([Int32(10), Int32(20)], Int32(5))
    print(f2.items)
    print(f2.x)

main()
