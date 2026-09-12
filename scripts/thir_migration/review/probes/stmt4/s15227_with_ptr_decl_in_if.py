from tpy import int32
class Res:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v
class RCM:
    r: Res
    def __init__(self, n: int32) -> None:
        self.r = Res(n)
    def __enter__(self) -> Res:
        return self.r
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print(self.r.v)

def f(n: int32) -> int32:
    if n > 0:
        with RCM(1) as r:
            print(r.v)
        with RCM(2) as r:
            print(r.v)
    return n
def main() -> None:
    print(f(1))
main()
