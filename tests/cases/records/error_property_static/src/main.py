# @property combined with @staticmethod should error
from tpy import Int32

class Foo:
    @staticmethod
    @property
    def x() -> Int32:  # tpyc: error(/cannot be combined/)
        return 0
