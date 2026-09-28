# Protocol types cannot be used as method return types
from typing import Sized

class BadRecord:
    def get_sized(self) -> Sized:  # tpyc: error(/cannot be used as a return type/)
        x: int = 1
