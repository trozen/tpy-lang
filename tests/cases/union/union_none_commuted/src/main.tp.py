# Commuted operand form: None is v / None is not v
from tpy import Int32

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def check(v: Int32 | Cat | None) -> str:
    if None is v:
        return "none"
    if isinstance(v, Int32):
        return "int"
    return "cat"

def check_not(v: Int32 | Cat | None) -> str:
    if None is not v:
        if isinstance(v, Int32):
            return "got int"
        return "got cat"
    return "got none"

def main() -> None:
    print(check(Int32(1)))
    print(check(Cat("Whiskers")))
    print(check(None))
    print(check_not(Int32(2)))
    print(check_not(Cat("Paws")))
    print(check_not(None))

main()
