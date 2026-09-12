# Commuted operand form: None is v / None is not v
from tpy import int32

class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def check(v: int32 | Cat | None) -> str:
    if None is v:
        return "none"
    if isinstance(v, int32):
        return "int"
    return "cat"

def check_not(v: int32 | Cat | None) -> str:
    if None is not v:
        if isinstance(v, int32):
            return "got int"
        return "got cat"
    return "got none"

def main() -> None:
    print(check(int32(1)))
    print(check(Cat("Whiskers")))
    print(check(None))
    print(check_not(int32(2)))
    print(check_not(Cat("Paws")))
    print(check_not(None))

main()
