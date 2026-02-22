from tpy.extern import native_c_global
from tpy import Int32

def main() -> None:
    x: Int32 = native_c_global("some_var")  # tpyc: error(/can only be used at module level/)
