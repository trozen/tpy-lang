# Tuple unpacking inside a loop
from tpy import int32

def make_pair(i: int32) -> tuple[int32, str]:
    if i == 0:
        return (int32(10), "ten")
    elif i == 1:
        return (int32(20), "twenty")
    else:
        return (int32(30), "thirty")

def main() -> None:
    total: int32 = 0
    for i in range(3):
        n, s = make_pair(int32(i))
        print(s)
        total = total + n
    print(total)

main()
