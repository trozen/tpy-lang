# Dict with int32 keys
from tpy import int32

def main() -> None:
    d = {int32(1): "one", int32(2): "two", int32(3): "three"}
    print(d)
    print(d[int32(2)])
    print(int32(1) in d)
    print(int32(99) in d)
    for k in d:
        print(k, d[k])

main()
