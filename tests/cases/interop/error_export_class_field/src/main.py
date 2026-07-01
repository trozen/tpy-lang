# A field of exposed-class type can't be a getset yet (a nested class field
# needs per-instance marshalling).
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class Inner:
    def __init__(self, v: Int64):
        self.v = v


@export
class Outer:
    def __init__(self, inner: Inner):
        self.inner = inner  # tpyc: error(/field 'inner' of exposed-class type 'Inner'/)
