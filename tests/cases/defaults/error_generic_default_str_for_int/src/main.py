# Error: default value "hi" is incompatible with T=Int32
from tpy import Int32

def f[T](x: T = "hi") -> T:
    return x

def main() -> None:
    a: str = f[str]()
    print(a)
    b: Int32 = f[Int32]()  # tpyc: error(/incompatible/)

main()
