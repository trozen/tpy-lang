# Error: default value 0 is incompatible with T=str
from tpy import int32

def f[T](x: T = 0) -> T:
    return x

def main() -> None:
    a: int32 = f[int32]()
    print(a)
    b: str = f[str]()  # tpyc: error(/incompatible/)

main()
