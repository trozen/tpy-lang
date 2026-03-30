# Internal module defining the error type.
from tpy import ReturnException, error_return

class ParseError(Exception, ReturnException):
    pass

@error_return(ParseError)
def parse_int(s: str) -> int:
    if s == "":
        raise ParseError
    return int(s)
