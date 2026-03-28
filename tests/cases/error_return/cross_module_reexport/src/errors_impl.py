# Internal module defining the error type.
from tpy import ControlFlow, error_return

class ParseError(Exception, ControlFlow):
    pass

@error_return(ParseError)
def parse_int(s: str) -> int:
    if s == "":
        raise ParseError
    return int(s)
