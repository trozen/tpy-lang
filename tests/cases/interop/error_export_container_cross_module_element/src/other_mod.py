# tpy: ext_module
from tpy import int64
from tpy.extern import export


@export
class Counter:
    def __init__(self, v: int64):
        self.value = v
