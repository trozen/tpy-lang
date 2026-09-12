# Short-circuit conditions with union None checks (and/or), RHS narrowing
from tpy import int32

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def test_and_flag(v: int32 | Dog | None, flag: bool) -> str:
    if v is not None and flag:
        if isinstance(v, int32):
            return "int+flag"
        return "dog+flag"
    return "skip"

def test_and_isinstance(v: int32 | Dog | None) -> str:
    if v is not None and isinstance(v, int32):
        return "int"
    return "other"

def test_or(v: int32 | Dog | None, w: int32 | Dog | None) -> str:
    if v is None or w is None:
        return "has none"
    return "both present"

def main() -> None:
    print(test_and_flag(int32(1), True))
    print(test_and_flag(int32(1), False))
    print(test_and_flag(None, True))
    print(test_and_isinstance(int32(5)))
    print(test_and_isinstance(Dog("Rex")))
    print(test_and_isinstance(None))
    print(test_or(int32(1), Dog("Rex")))
    print(test_or(None, Dog("Rex")))
    print(test_or(int32(1), None))

main()
