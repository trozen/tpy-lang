# except ControlFlow catches any @error_return exception regardless of concrete type
from tpy import Int32, error_return, ControlFlow

class ParseError(Exception, ControlFlow):
    pass

class NotFound(Exception, ControlFlow):
    pass

@error_return(ParseError)
def parse_digit(s: str) -> Int32:
    if s == "1":
        return 1
    raise ParseError

@error_return(NotFound)
def lookup(items: list[Int32], target: Int32) -> Int32:
    for i in range(len(items)):
        if items[i] == target:
            return i
    raise NotFound

def main() -> None:
    items: list[Int32] = [10, 20]

    # Success: both calls succeed
    try:
        v = parse_digit("1")
        idx = lookup(items, 20)
    except ControlFlow:
        print("error")
    else:
        print(v)
        print(idx)

    # ParseError triggers the catch-all
    try:
        v2 = parse_digit("x")
        print("unreachable")
    except ControlFlow:
        print("caught parse error")

    # NotFound triggers the catch-all
    try:
        idx2 = lookup(items, 99)
        print("unreachable")
    except ControlFlow:
        print("caught not found")

main()
