# Dict with Int32 keys
from tpy import Int32

def main() -> None:
    d = {Int32(1): "one", Int32(2): "two", Int32(3): "three"}
    print(d)
    print(d[Int32(2)])
    print(Int32(1) in d)
    print(Int32(99) in d)
    for k in d:
        print(k, d[k])

main()
