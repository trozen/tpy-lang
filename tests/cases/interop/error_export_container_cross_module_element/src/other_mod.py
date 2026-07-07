# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class Counter:
    def __init__(self, v: Int64):
        self.value = v
