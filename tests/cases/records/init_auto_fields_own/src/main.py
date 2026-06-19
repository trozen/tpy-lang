# An inferred field from an `Own[T]` param becomes a `T` field (a field owns its
# value inline); the @nocopy payload is moved in, so a stray copy is a hard error.
from tpy import Own, nocopy

@nocopy
class Res:
    def __init__(self, n: int):
        self.n = n

class Holder:
    def __init__(self, r: Own[Res]):
        self.r = r
    def value(self) -> int:
        return self.r.n

def main() -> None:
    h = Holder(Res(7))
    print(h.value())

main()
