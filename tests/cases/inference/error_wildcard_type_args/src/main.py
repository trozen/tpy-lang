# Error: wrong number of _ wildcard type arguments
from tpy import int32, int64

def pair_func[T, U](a: T, b: U) -> T:
    return a

def main() -> None:
    pair_func[_, _, _](int32(1), int64(2))  # tpyc: error(/expects 2 type argument/)

main()
