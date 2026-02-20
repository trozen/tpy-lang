# Reassignment to None kills narrowing facts; re-narrowing works after
from tpy import Int32

class Dog:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name

def test_reassign_to_none() -> str:
    v: Int32 | Dog | None = Int32(5)
    if v is not None:
        v = None
        if v is None:
            return "reassigned to none"
    return "was none"

def test_init_none_then_assign() -> str:
    v: Int32 | Dog | None = None
    v = Int32(42)
    if v is not None:
        if isinstance(v, Int32):
            return "got int"
        return "got dog"
    return "none"

def main() -> None:
    print(test_reassign_to_none())
    print(test_init_none_then_assign())

main()
