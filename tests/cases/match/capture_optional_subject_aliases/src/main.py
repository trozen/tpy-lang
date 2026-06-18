# A bare capture of a pointer-repr Optional subject aliases the subject (binds
# the pointer directly, no address-of), so mutation through it is visible.
class Box:
    def __init__(self, v: int):
        self.v = v

def main():
    b: Box | None = Box(1)
    match b:
        case q:
            q.v = 99       # q aliases b's Box
    if b is not None:
        print(b.v)         # 99

main()
