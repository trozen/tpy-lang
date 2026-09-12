# field(default=...) and field(default_factory=...) for @dataclass
from dataclasses import dataclass, field
from tpy import int32

@dataclass
class Point:
    x: int32 = 0
    y: int32 = 0

@dataclass
class Config:
    name: str
    value: int32 = field(default=42)
    tags: list[str] = field(default_factory=list)
    lookup: dict[str, int32] = field(default_factory=dict)

# Non-generic user type as factory default
@dataclass
class Canvas:
    name: str
    origin: Point = field(default_factory=Point)

def main() -> None:
    # All defaults
    c1 = Config("test")
    print(c1.name)
    print(c1.value)
    print(len(c1.tags))
    print(len(c1.lookup))
    # Override some defaults
    c2 = Config("prod", 99, ["a", "b"])
    print(c2.name)
    print(c2.value)
    print(len(c2.tags))
    # Each instance gets its own list/dict (no sharing)
    c1.tags.append("x")
    print(len(c1.tags))
    print(len(c2.tags))
    # Non-generic user type as factory default
    cv = Canvas("drawing")
    print(cv.origin)
    cv2 = Canvas("art", Point(10, 20))
    print(cv2.origin)

main()
