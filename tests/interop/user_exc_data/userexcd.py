# tpy: ext_module
# A data-carrying user exception under the warn-and-cross policy: its type and
# message DO cross the @export boundary (strictly better than degrading to the
# built-in base), but its instance fields do NOT. The shared driver reads only
# type + message (which agree with CPython); ext_checks.py asserts the dropped
# field against the compiled .so. The raise also emits the data-field warning at
# compile time (asserted by tests/cases/interop/warn_export_user_exc_data).
from tpy import Int32, Int64
from tpy.extern import export


class ParseError(ValueError):
    line: Int32

    def __init__(self, message: str, line: Int32):
        self.message = message
        self.line = line


@export
def parse(n: Int64) -> Int64:
    if n < 0:
        raise ParseError("bad input", 7)
    return n
