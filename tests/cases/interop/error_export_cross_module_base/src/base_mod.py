# tpy: ext_module
from tpy import int64
from tpy.extern import export


@export
class Imported:
    x: int64

    def __init__(self, x: int64):
        self.x = x
