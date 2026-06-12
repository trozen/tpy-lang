# guarded case None falls through to wildcard/capture on guard failure;
# the capture binds the full Int32 | None (a guarded None arm does not
# cover the None side), so the body narrows before using the value
from typing import Optional
from tpy import Int32

def with_wildcard(x: Optional[Int32], flag: bool) -> str:
    match x:
        case None if flag:
            return "none+flag"
        case _:
            return "other"

def with_capture(x: Optional[Int32], flag: bool) -> str:
    match x:
        case None if flag:
            return "none+flag"
        case v:
            if v is None:
                return "none-noflag"
            return str(v)

def main() -> None:
    print(with_wildcard(None, True))
    print(with_wildcard(None, False))
    print(with_wildcard(Int32(5), True))
    print(with_wildcard(Int32(5), False))
    print(with_capture(None, True))
    print(with_capture(None, False))
    print(with_capture(Int32(5), True))
    print(with_capture(Int32(5), False))

main()
