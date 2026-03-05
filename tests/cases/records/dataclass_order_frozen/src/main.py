# @dataclass(frozen=True, order=True) combines ordering, equality, and hashing
from dataclasses import dataclass
from tpy import Int32

@dataclass(frozen=True, order=True)
class Version:
    major: Int32
    minor: Int32
    patch: Int32

def main() -> None:
    v1 = Version(1, 0, 0)
    v2 = Version(2, 0, 0)
    v3 = Version(1, 1, 0)
    # Ordering
    print(v1 < v2)
    print(v1 < v3)
    print(v2 > v3)
    # Equality
    print(v1 == Version(1, 0, 0))
    print(v1 != v2)
    # Hash (from frozen) -- usable as dict key
    d: dict[Version, str] = {v1: "one", v2: "two"}
    print(d[v1])
    print(d[v2])

main()
