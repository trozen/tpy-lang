# Error: wrong number of _ wildcard type arguments
from tpy import Int32, Int64

def pair_func[T, U](a: T, b: U) -> T:
    return a

def main() -> None:
    pair_func[_, _, _](Int32(1), Int64(2))  # tpyc: error(/expects 2 type argument/)

main()
