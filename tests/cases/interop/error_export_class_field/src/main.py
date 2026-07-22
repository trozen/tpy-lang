# A reassignable field of exposed-class type can't be a getset: a borrow
# view would read the storage slot through the post-__init__ rebind. (A
# never-reassigned field crosses as a read-only view -- tests/interop/
# field_views.)
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
        self.inner = inner  # tpyc: error(/field 'inner' of exposed-class type 'Inner'.*never reassigned/)

    def replace(self, inner: Inner) -> None:
        self.inner = inner
