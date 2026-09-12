# @dataclass(frozen=True) inheritance: both parent and child frozen
from dataclasses import dataclass
from tpy import int32

@dataclass(frozen=True)
class Vec2:
    x: int32
    y: int32

@dataclass(frozen=True)
class Vec3(Vec2):
    z: int32

def main() -> None:
    v = Vec3(1, 2, 3)
    print(v)
    print(v.x)
    print(v.z)
    # Equality with all fields
    print(v == Vec3(1, 2, 3))
    print(v == Vec3(1, 2, 4))
    # Hash works (frozen)
    d: dict[Vec3, str] = {v: "a"}
    print(d[Vec3(1, 2, 3)])

main()
