# Cross-module @error_return with auto-propagation.
# Tests that error type C++ qualification works correctly when
# error types are defined in a different module.
from tpy import error_return
from errors_impl import ParseError, parse_int

@error_return(ParseError)
def parse_pair(a: str, b: str) -> int:
    x = parse_int(a)
    y = parse_int(b)
    return x + y

def main() -> None:
    try:
        v = parse_pair("10", "20")
    except ParseError:
        print("error")
    else:
        print(v)

    try:
        v2 = parse_pair("10", "")
    except ParseError:
        print("caught error")
    else:
        print(v2)

main()
