# Error: default value "hi" is incompatible with T=int32
from tpy import int32

def f[T](x: T = "hi") -> T:
    return x

def main() -> None:
    a: str = f[str]()
    print(a)
    b: int32 = f[int32]()  # tpyc: error(/incompatible/)

main()
