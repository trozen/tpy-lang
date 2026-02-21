from tpy import native_c_global, Int32

def main() -> None:
    x: Int32 = native_c_global("some_var")  # tpyc: error(/can only be used at module level/)
