# Char str-like behavior: concat, repeat, len, ord(str), Char(str).
from tpy import Char


def main():
    c: Char = "A"

    # str + Char
    print("hello" + c)

    # Char + str
    print(c + "hello")

    # Char + Char
    print(c + c)

    # Char * int / int * Char
    print(c * 3)
    print(3 * c)

    # len(Char) -- always 1
    print(len(c))

    # ord(str) -- single char string
    s = "B"
    print(ord(s))

    # ord(Char) still works
    print(ord(c))

    # Char(str) constructor
    s2 = "Z"
    z = Char(s2)
    print(z)


main()
