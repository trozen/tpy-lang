# Error: wrong number of _ wildcard type arguments on method
from tpy import Int32, Int64

class Mapper:
    def transform[U, V](self, u: U, v: V) -> V:
        return v

def main() -> None:
    m = Mapper()
    m.transform[_, _, _](Int32(1), Int64(2))  # tpyc: error(/expects 2 type argument/)

main()
