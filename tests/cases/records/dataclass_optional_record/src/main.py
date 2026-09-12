# Test @dataclass with Optional[Record] field default (std::nullopt codegen fix).
from tpy import int32
from dataclasses import dataclass

@dataclass
class Inner:
    x: int32

@dataclass
class Outer:
    name: str
    inner: Inner | None = None

def main() -> None:
    o1 = Outer("a", Inner(42))
    print(o1)

    o2 = Outer("b")
    print(o2)

    o3 = Outer("c", None)
    print(o3)

    print(o1 == o2)
    print(o2 == o3)
    print(o1 == Outer("a", Inner(42)))

main()
