from tpy.extern import native_global
from tpy import Int32

x: Int32 = native_global(123, binding="C")  # tpyc: error(/argument must be a string literal/)

def main() -> None:
    pass
