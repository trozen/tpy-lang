# Int32 | None canonicalizes to Optional[Int32], not UnionType -- regression guard
from tpy import Int32

def check(v: Int32 | None) -> str:
    if v is None:
        return "none"
    return "has value"

def main() -> None:
    print(check(Int32(42)))
    print(check(None))

main()
