# Assert with expression messages (variables, method calls, field access).
from tpy import int32

class Error:
    message: str
    def __init__(self, message: str) -> None:
        self.message = message

def check_positive(n: int32, msg: str) -> int32:
    assert n > 0, msg
    return n

def check_error(n: int32, e: Error) -> int32:
    assert n > 0, e.message
    return n

def main() -> None:
    print(check_positive(5, "must be positive"))
    print(check_error(3, Error("bad value")))

main()
