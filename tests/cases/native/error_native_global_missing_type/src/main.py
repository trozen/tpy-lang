from tpy.extern import native_global

x = native_global("some_var", binding="C")  # tpyc: error(/requires a type annotation/)

def main() -> None:
    pass
