# Exception types with data fields and except...as e binding
from tpy import int32, error_return, ReturnException

class ParseError(Exception, ReturnException):
    line: int32
    column: int32
    detail: str

    def __init__(self, line: int32, column: int32, detail: str) -> None:
        self.line = line
        self.column = column
        self.detail = detail

@error_return(ParseError)
def parse(s: str) -> int32:
    if s == "ok":
        return 42
    raise ParseError(10, 5, "unexpected token")

def main() -> None:
    # Happy path
    try:
        v = parse("ok")
    except ParseError as e:
        print(e.line)
    else:
        print(v)

    # Error path with field access
    try:
        v2 = parse("bad")
    except ParseError as e:
        print(e.line)
        print(e.column)
        print(e.detail)
    else:
        print(v2)

    # No binding (as e not used)
    try:
        v3 = parse("bad")
    except ParseError:
        print("error without binding")
    else:
        print(v3)

main()
