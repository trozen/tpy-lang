# Error: make_default() inside unbounded generic -- T has no Default bound
from tpy import make_default

def bad[T]() -> T:
    return make_default()  # tpyc: error(/Default/)

def main() -> None:
    bad[int]()

main()
