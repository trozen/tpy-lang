# Return-tier try/except with finally block (goto-based + FinallyGuard)
from tpy import Int32, error_return, ReturnException

class NotFound(Exception, ReturnException):
    pass

@error_return(NotFound)
def lookup(key: str) -> Int32:
    if key == "x":
        return 42
    raise NotFound

def main() -> None:
    # Success path: finally runs after try+else
    try:
        v = lookup("x")
    except NotFound:
        print("not found")
    else:
        print(v)
    finally:
        print("finally 1")

    # Error path: finally runs after except
    try:
        v2 = lookup("y")
    except NotFound:
        print("not found")
    else:
        print(v2)
    finally:
        print("finally 2")

main()
