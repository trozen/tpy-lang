# @property combined with @staticmethod should error
from tpy import int32

class Foo:
    @staticmethod
    @property
    def x() -> int32:  # tpyc: error(/cannot be combined/)
        return 0
