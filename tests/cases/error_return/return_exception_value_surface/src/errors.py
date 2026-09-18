# A return exception with a declared message, defined away from its users: the
# header another module includes carries the field and the str() accessor.
from tpy import int32, error_return, ReturnException


class Denied(Exception, ReturnException):
    message: str

    def __init__(self, message: str) -> None:
        self.message = message


@error_return(Denied)
def check(level: int32) -> int32:
    if level > 2:
        return level
    raise Denied("level too low")
