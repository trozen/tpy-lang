# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class Foo:
    def __init__(self, value: Int64):
        self.value = value

    def get(self) -> Int64:
        return self.value
