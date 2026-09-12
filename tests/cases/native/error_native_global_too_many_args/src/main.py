from tpy.extern import native_global
from tpy import int32

x: int32 = native_global("a", "b")  # tpyc: error(/takes 0 or 1 arguments/)

def main() -> None:
    pass
