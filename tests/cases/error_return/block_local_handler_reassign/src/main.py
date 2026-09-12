# Block-local scoping through the return-tier except handler (a caller catching
# an @error_return callee), which emits handler bodies on its own path. These
# shapes compile on either side of the scope-revoke change -- sema pre-declares
# a name assigned both inside and after the try, so the fresh-declaration path
# is never reached here. Kept as coverage of an otherwise untested interaction,
# not as a regression guard.
from tpy import int32, error_return, ReturnException


class ParseError(Exception, ReturnException):
    pass


@error_return(ParseError)
def parse_digit(s: str) -> int32:
    if s == "0":
        return 0
    if s == "1":
        return 1
    raise ParseError


def handler_declares(s: str) -> int32:
    try:
        d = parse_digit(s)
        print(d)
    except ParseError:
        n = -1
        print(n)
    n = 9
    return n


def handler_binding_declares(s: str) -> int32:
    # Same shape through the `as e` binding arm, which emits the handler body
    # inside an extra brace block.
    try:
        d = parse_digit(s)
        print(d)
    except ParseError as e:
        n = -2
        print(n)
    n = 8
    return n


def try_body_declares(s: str) -> int32:
    # The try body is the sibling block; its declaration must not reach the
    # post-try assignment either.
    try:
        v = parse_digit(s)
        print(v)
    except ParseError:
        pass
    v = 7
    return v


def main() -> None:
    print(handler_declares("1"))
    print(handler_declares("x"))
    print(handler_binding_declares("0"))
    print(handler_binding_declares("x"))
    print(try_body_declares("1"))
    print(try_body_declares("x"))


main()
