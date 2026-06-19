# The fall-through narrowed access must alias the member (not copy): mutate a
# field through it and observe the change on the original object.
class Box:
    def __init__(self, n: int):
        self.n = n
class Other:
    def __init__(self, s: int):
        self.s = s

def bump(x: Box | Other) -> None:
    if isinstance(x, Other):
        return
    x.n += 1            # x is Box; write through the narrowed alias

def main() -> None:
    b = Box(5)
    bump(b)
    print(b.n)          # 6 -> the narrowed access aliased, did not copy

main()
