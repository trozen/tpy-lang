from tpy import native_global, Int32

x: Int32 = native_global("a", "b")  # tpyc: error(/takes 0 or 1 arguments/)

def main() -> None:
    pass
