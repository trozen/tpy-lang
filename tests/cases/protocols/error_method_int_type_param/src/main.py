# A method cannot declare an integer type parameter yet.
from tpy import int32


class R:
    def m[N: int](self) -> int32:  # tpyc: error(/Method 'R.m': integer type parameters \(`N: int`\) are not yet supported/)
        return 1
