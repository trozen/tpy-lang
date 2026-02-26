# IntEnum: arithmetic, ordering, and int comparison
from enum import IntEnum

class Priority(IntEnum):
    Low = 0
    Medium = 1
    High = 2

def main() -> None:
    p: Priority = Priority.High

    # Ordering between IntEnum members
    print(Priority.Low < Priority.High)
    print(Priority.High > Priority.Medium)
    print(Priority.Low <= Priority.Low)
    print(Priority.High >= Priority.Medium)

    # Equality with int
    print(p == 2)
    print(p != 1)
    print(Priority.Low == 0)

    # Arithmetic with int (result is int, not Priority)
    x: int = Priority.Medium + 10
    print(x)
    y: int = 10 + Priority.Medium
    print(y)
    z: int = Priority.High * 3
    print(z)
    w: int = Priority.High - 1
    print(w)

main()
