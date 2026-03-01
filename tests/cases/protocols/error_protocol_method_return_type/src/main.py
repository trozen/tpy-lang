# Protocol types cannot be used as method return types
from tpy import Sized

class BadRecord:  # tpyc: error(/cannot be used as a return type/)
    def get_sized(self) -> Sized:
        x: int = 1
