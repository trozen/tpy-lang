# Error: wrong number of _ wildcard type arguments on method
from tpy import int32, int64

class Mapper:
    def transform[U, V](self, u: U, v: V) -> V:
        return v

def main() -> None:
    m = Mapper()
    m.transform[_, _, _](int32(1), int64(2))  # tpyc: error(/expects 2 type argument/)

main()
