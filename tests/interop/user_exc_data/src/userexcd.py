# tpy: ext_module
# Faithful data-field crossing: a data-carrying user exception raised at the
# @export boundary delivers its instance fields (scalar / str / bytes / exposed
# enum) as CPython instance attributes, via PyErr_SetObject over a constructed
# instance. The driver reads type + every data field -- all agree with CPython.
# str(e) and e.args reflect the message field only (the C++ exception holds
# typed fields, not the original constructor arg tuple), an acknowledged
# divergence asserted ext-only in ext_checks.py. That divergence is
# documented, not warned -- it is unavoidable with no author-side action.
from tpy import int32, int64
from tpy.extern import export
from enum import IntEnum


@export
class Severity(IntEnum):
    WARN = 1
    FATAL = 2


class ParseError(ValueError):
    line: int32
    detail: str
    payload: bytes
    severity: Severity

    def __init__(self, message: str, line: int32, detail: str,
                 payload: bytes, severity: Severity):
        super().__init__(message)
        self.line = line
        self.detail = detail
        self.payload = payload
        self.severity = severity


@export
def parse(n: int64) -> int64:
    if n < 0:
        raise ParseError("bad input", 7, "unexpected token", b"raw", Severity.FATAL)
    return n
