# @dataclass inheritance with defaults on child fields
from dataclasses import dataclass
from tpy import Int32

@dataclass
class Point:
    x: Int32
    y: Int32

@dataclass
class Point3D(Point):
    z: Int32 = 0

def main() -> None:
    # Use default for z
    p1 = Point3D(1, 2)
    print(p1.x)
    print(p1.y)
    print(p1.z)
    print(p1)
    # Override z
    p2 = Point3D(1, 2, 99)
    print(p2.z)
    # Equality
    print(p1 == Point3D(1, 2, 0))
    print(p1 == p2)

main()
