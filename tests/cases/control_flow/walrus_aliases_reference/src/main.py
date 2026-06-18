# A walrus binding of a reference-type lvalue aliases the source (mutation
# through the walrus target is visible on the source), like CPython.
class Box:
    def __init__(self, v: int):
        self.v = v

def main():
    b = Box(5)
    print((q := b).v)       # 5
    q.v = 99
    print(b.v)              # 99 -- q aliased b

main()
