# basic_slice() and slice() constructors for creating slice objects.
from tpy import int32, basic_slice

def main() -> None:
    items: list[int32] = [10, 20, 30, 40, 50]

    # basic_slice constructor
    s = basic_slice(1, 4)
    print(items[s])

    # slice constructor (with step)
    s2 = slice(0, 5, 2)
    print(items[s2])

    # basic_slice on string
    text = "hello world"
    s3 = basic_slice(0, 5)
    print(text[s3])

    # printing
    print(s2)

    # field access
    print(s.start)
    print(s.stop)
    print(s2.step)

    # None args (open-ended slices)
    s4 = basic_slice(None, 3)
    print(items[s4])

    s5 = basic_slice(2, None)
    print(items[s5])

    s6 = slice(None, None, 2)
    print(items[s6])

main()
