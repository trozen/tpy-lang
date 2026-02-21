from tpy import native_c_global

x = native_c_global("some_var")  # tpyc: error(/requires a type annotation/)

def main() -> None:
    pass
