# extend() with own_iter() moves elements instead of copying.
from tpy import int32, own_iter

def main() -> None:
    a: list[int32] = [1, 2, 3]
    b: list[int32] = [10, 20]
    b.extend(own_iter(a))
    print(b)

main()
