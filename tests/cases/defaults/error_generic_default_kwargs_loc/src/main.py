# Error loc: gap-filled default should point to call site, not function definition
from tpy import Int32

def f[T](a: T, b: T = 0) -> None:
    pass

def main() -> None:
    f[str](a="hi")  # tpyc: error(/incompatible/)

main()
