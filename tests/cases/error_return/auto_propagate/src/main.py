# Auto-propagation: @error_return(E) functions forward matching errors without try/except
from tpy import int32, error_return, ReturnException

class ParseError(Exception, ReturnException):
    pass

@error_return(ParseError)
def parse_digit(s: str) -> int32:
    if s == "0":
        return 0
    if s == "1":
        return 1
    raise ParseError

@error_return(ParseError)
def validate(s: str) -> None:
    if s == "":
        raise ParseError

@error_return(ParseError)
def parse_two_digits(a: str, b: str) -> int32:
    validate(a)  # ExprStmt propagation (void call, result discarded)
    x = parse_digit(a)
    y = parse_digit(b)
    return x * 10 + y

def main() -> None:
    try:
        v = parse_two_digits("1", "0")
    except ParseError:
        print("error")
    else:
        print(v)

    try:
        v2 = parse_two_digits("x", "0")
    except ParseError:
        print("error")
    else:
        print(v2)

main()
