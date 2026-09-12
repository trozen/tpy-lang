from tpy.extern import native_global
from tpy import int32

def main() -> None:
    x: int32 = native_global("some_var", binding="C")  # tpyc: error(/can only be used at module level/)
