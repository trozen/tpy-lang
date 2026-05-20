# Phase 20 invariant: dynamic-type preservation also works for a
# user-defined exception subclass. Native-class coverage is in
# box_throwable_preserves_dynamic_type; this case verifies the same
# behavior reaches user-extension classes via the same mechanism.
#
# Pre-Stage-4: BaseException is still @native; user subclass inherits the
# C++ ctor + macro-emitted clone()/__raise__() through the existing
# inheritance chain.
from tplib import Box
from tpy import Throwable, Int32


class ParseError(Exception):
    line: Int32
    def __init__(self, message: str, line: Int32) -> None:
        self.message = message
        self.line = line


def main() -> None:
    try:
        raise ParseError("unexpected token", 42)
    except BaseException as e:
        stored: Box[Throwable] = Box(e.clone())
        try:
            raise stored
        except ParseError as pe:
            print("caught as ParseError:", pe.message, pe.line)
        except BaseException:
            print("caught as BaseException -- slicing!")


main()
