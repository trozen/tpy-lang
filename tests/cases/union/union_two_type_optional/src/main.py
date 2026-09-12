# int32 | None canonicalizes to Optional[int32], not UnionType -- regression guard
from tpy import int32

def check(v: int32 | None) -> str:
    if v is None:
        return "none"
    return "has value"

def main() -> None:
    print(check(int32(42)))
    print(check(None))

main()
