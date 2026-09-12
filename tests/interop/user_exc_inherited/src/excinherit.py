# tpy: ext_module
# Inherited data fields + transitive raise: a subclass of a data-carrying user
# exception crosses BOTH its own and its inherited data fields as CPython
# instance attributes, and the crossing is registry-driven so it fires even when
# the exception is raised from a callee (not directly in the @export body).
from tpy import int32, int64
from tpy.extern import export


class BaseErr(Exception):
    code: int32

    def __init__(self, message: str, code: int32):
        self.message = message
        self.code = code


class DerivedErr(BaseErr):
    detail: str

    def __init__(self, message: str, code: int32, detail: str):
        self.message = message
        self.code = code
        self.detail = detail


def _check(n: int64) -> int64:   # callee -- the raise reaches the boundary transitively
    if n < 0:
        raise DerivedErr("bad", 7, "deep")
    return n


@export
def run(n: int64) -> int64:
    return _check(n)
