# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class Imported:
    x: Int64

    def __init__(self, x: Int64):
        self.x = x
