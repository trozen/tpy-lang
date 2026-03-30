# Bare raise in return-tier except block re-propagates the error
from tpy import Int32, error_return, ControlFlow

class NotFound(Exception, ControlFlow):
    code: Int32

    def __init__(self, code: Int32) -> None:
        self.code = code

@error_return(NotFound)
def inner(key: str) -> Int32:
    if key == "x":
        return 42
    raise NotFound(99)

@error_return(NotFound)
def outer(key: str) -> Int32:
    try:
        v = inner(key)
    except NotFound as e:
        print(e.code)
        raise
    return v

def main() -> None:
    # Happy path
    try:
        v = outer("x")
    except NotFound as e:
        print(e.code)
    else:
        print(v)

    # Error path: inner raises, outer re-raises
    try:
        v2 = outer("y")
    except NotFound as e:
        print(e.code)
    else:
        print(v2)

main()
