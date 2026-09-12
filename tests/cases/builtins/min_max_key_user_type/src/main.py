# min/max with key returning a user-defined Comparable type
from __future__ import annotations
from tpy import int32, Own

class Priority:
    level: int32
    def __init__(self, level: int32) -> None:
        self.level = level
    def __lt__(self, other: Priority) -> bool:
        return self.level < other.level

class Task:
    name: str
    prio: Priority
    def __init__(self, name: str, prio: Own[Priority]) -> None:
        self.name = name
        self.prio = prio

def main() -> None:
    a = Task("low", Priority(1))
    b = Task("high", Priority(3))
    c = Task("mid", Priority(2))

    # min/max by user-defined Comparable key
    print(min(a, b, key=lambda t: t.prio).name)
    print(max(a, b, key=lambda t: t.prio).name)

    # 3-arg
    print(min(a, b, c, key=lambda t: t.prio).name)
    print(max(a, b, c, key=lambda t: t.prio).name)

main()
