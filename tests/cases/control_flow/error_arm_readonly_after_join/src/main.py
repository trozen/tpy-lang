# A local rebound to a readonly reference in one arm stays readonly after
# every join on the way out -- the if, the return-tier try, the throw-tier
# try and the match -- so the write after the match is refused.
from tpy import int32, readonly, error_return, ReturnException


class ParseError(Exception, ReturnException):
    pass


@error_return(ParseError)
def parse_digit(s: str) -> int32:
    if s == "0":
        return 0
    raise ParseError


class Box:
    def __init__(self, v: int32) -> None:
        self.v = v


def f(k: int32, s: str, other: Box, ro: readonly[Box]) -> None:
    x = other
    match k:
        case 1:
            try:
                if len(s) > 1:
                    raise ValueError("long")
            except ValueError:
                try:
                    parse_digit(s)
                    if s == "00":
                        x = ro
                except ParseError:
                    pass
        case _:
            pass
    # the slot may hold the readonly `ro` here
    x.v = 5  # tpyc: error(/Cannot mutate readonly reference/)
    print(x.v)


f(1, "00", Box(0), Box(0))
