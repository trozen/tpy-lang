# tpy: ext_module
# Inherited data fields + transitive raise: a subclass of a data-carrying user
# exception crosses BOTH its own and its inherited data fields as CPython
# instance attributes, and the crossing is registry-driven so it fires even when
# the exception is raised from a callee (not directly in the @export body).
from tpy import Int32, Int64
from tpy.extern import export


class BaseErr(Exception):
    code: Int32

    def __init__(self, message: str, code: Int32):
        self.message = message
        self.code = code


class DerivedErr(BaseErr):
    detail: str

    def __init__(self, message: str, code: Int32, detail: str):
        self.message = message
        self.code = code
        self.detail = detail


def _check(n: Int64) -> Int64:   # callee -- the raise reaches the boundary transitively
    if n < 0:
        raise DerivedErr("bad", 7, "deep")
    return n


@export
def run(n: Int64) -> Int64:
    return _check(n)
