# A borrow-form container return (`-> list[T]`, not `-> Own[list[T]]`) from an
# @export function or exposed-class method is copied out at the boundary, so
# write-through aliasing is lost -- warn at the return, mirroring the exposed
# class `-> Cls` alias warning; Own[...] is the acknowledged spelling and stays
# quiet. (The def lines also carry the copy-in warning: a returned borrow can
# be mutated through by the caller in TPy, so sema marks the param mutated.)
# tpy: ext_module
from tpy import int64, Own
from tpy.extern import export


@export
class Holder:
    n: int64

    def __init__(self, n: int64):
        self.n = n

    def same(self, xs: list[int64]) -> list[int64]:  # tpyc: warning(/list parameter 'xs' is copied in/)
        return xs  # tpyc: warning(/method 'same': returns a list by reference.*copied across the CPython boundary.*return Own/)

    def fresh(self, xs: list[int64]) -> Own[list[int64]]:  # tpyc: ok
        out: list[int64] = []
        for x in xs:
            out.append(x)
        return out


@export
def pick(d: dict[str, int64]) -> dict[str, int64]:  # tpyc: warning(/dict parameter 'd' is copied in/)
    return d  # tpyc: warning(/function 'pick': returns a dict by reference.*copied across the CPython boundary.*return Own/)
