__tpy__ = {"mod": "ext"}
from tpy import int64
from tpy.extern import export


@export
class Foo:
    def __init__(self, value: int64):
        self.value = value
