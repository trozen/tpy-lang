# except ReturnException catches any @error_return exception regardless of concrete type
from tpy import int32, error_return, ReturnException

class ParseError(Exception, ReturnException):
    pass

class NotFound(Exception, ReturnException):
    pass

@error_return(ParseError)
def parse_digit(s: str) -> int32:
    if s == "1":
        return 1
    raise ParseError

@error_return(NotFound)
def lookup(items: list[int32], target: int32) -> int32:
    for i in range(len(items)):
        if items[i] == target:
            return i
    raise NotFound

def main() -> None:
    items: list[int32] = [10, 20]

    # Success: both calls succeed
    try:
        v = parse_digit("1")
        idx = lookup(items, 20)
    except ReturnException:
        print("error")
    else:
        print(v)
        print(idx)

    # ParseError triggers the catch-all
    try:
        v2 = parse_digit("x")
        print("unreachable")
    except ReturnException:
        print("caught parse error")

    # NotFound triggers the catch-all
    try:
        idx2 = lookup(items, 99)
        print("unreachable")
    except ReturnException:
        print("caught not found")

main()
