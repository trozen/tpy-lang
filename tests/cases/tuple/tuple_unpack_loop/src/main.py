# Tuple unpacking inside a loop
from tpy import Int32

def make_pair(i: Int32) -> tuple[Int32, str]:
    if i == 0:
        return (Int32(10), "ten")
    elif i == 1:
        return (Int32(20), "twenty")
    else:
        return (Int32(30), "thirty")

def main() -> None:
    total: Int32 = 0
    for i in range(3):
        n, s = make_pair(Int32(i))
        print(s)
        total = total + n
    print(total)

main()
