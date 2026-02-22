from tpy.extern import native_c_global
from tpy import Int32

x: Int32 = native_c_global(123)  # tpyc: error(/argument must be a string literal/)

def main() -> None:
    pass
