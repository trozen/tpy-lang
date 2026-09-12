# **kwargs is rejected at the @export boundary: the wrapper unpacks a fixed
# kwlist of named slots, so an open-ended keyword pack has nowhere to land and
# would be silently dropped. (*args is rejected next door for the same reason.)
# tpy: ext_module
from typing import TypedDict, Unpack

from tpy import int64
from tpy.extern import export


class Opts(TypedDict):
    scale: int64


@export
def f(a: int64, **kw: Unpack[Opts]) -> int64:  # tpyc: error(/\*\*kwargs is not supported/)
    return a
