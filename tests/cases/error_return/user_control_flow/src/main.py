# User-defined ControlFlow exception type with @error_return
from tpy import Int32, error_return, ControlFlow

class ParseError(Exception, ControlFlow):
    pass

@error_return(ParseError)
def parse_digit(s: str) -> Int32:
    if s == "0":
        return 0
    if s == "1":
        return 1
    raise ParseError

def main() -> None:
    try:
        v = parse_digit("1")
    except ParseError:
        print("error")
    else:
        print(v)

    try:
        v2 = parse_digit("x")
    except ParseError:
        print("error")
    else:
        print(v2)

main()
