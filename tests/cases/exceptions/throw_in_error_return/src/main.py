# Throw-tier exception inside an @error_return function
# The error_return handles the expected failure; throw handles the unexpected
from tpy import int32, error_return, ReturnException

class NotFound(Exception, ReturnException):
    pass

class BadKey(Exception):
    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key

    key: str

@error_return(NotFound)
def lookup(key: str) -> int32:
    if key == "":
        raise BadKey(key)
    if key == "x":
        return 42
    raise NotFound

def main() -> None:
    # Happy path
    try:
        v = lookup("x")
    except NotFound:
        print("not found")
    else:
        print(v)

    # Return-tier error (NotFound)
    try:
        v2 = lookup("y")
    except NotFound:
        print("not found")
    else:
        print(v2)

    # Throw-tier error (BadKey) propagates through error_return
    # Need two nested try blocks: inner handles NotFound, outer catches BadKey
    try:
        try:
            v3 = lookup("")
        except NotFound:
            print("not found")
    except BadKey as e:
        print("bad key: " + e.key)

main()
