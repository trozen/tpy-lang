from tpy import Int32
class Res:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
class RCM:
    r: Res
    def __init__(self, n: Int32) -> None:
        self.r = Res(n)
    def __enter__(self) -> Res:
        return self.r
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print(self.r.v)

with RCM(1) as r:
    print(r.v)
with RCM(2) as r:
    print(r.v)
print(r.v)
