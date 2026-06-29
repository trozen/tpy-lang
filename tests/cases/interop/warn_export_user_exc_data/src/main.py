# A direct `raise` of a user exception with instance fields in an @export body
# warns: only its type + message cross the boundary, not the data fields.
# tpy: ext_module
from tpy import Int32, Int64
from tpy.extern import export


class ParseError(ValueError):
    line: Int32

    def __init__(self, message: str, line: Int32):
        self.message = message
        self.line = line


class NotFound(KeyError):  # message-only: the message IS the only state
    def __init__(self, message: str):
        self.message = message


@export
def parse(n: Int64) -> Int64:
    if n < 0:
        raise ParseError("bad input", 7)  # tpyc: warning(/data field.*line.*do not cross/)
    return n


# A message-only user exception (NotFound) must NOT warn -- the negative case.
@export
def lookup(n: Int64) -> Int64:
    if n < 0:
        raise NotFound("missing")  # tpyc: ok
    return n
