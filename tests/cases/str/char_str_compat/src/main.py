# char str-like behavior: concat, repeat, len, ord(str), char(str).
from tpy import char


def main():
    c: char = "A"

    # str + char
    print("hello" + c)

    # char + str
    print(c + "hello")

    # char + char
    print(c + c)

    # char * int / int * char
    print(c * 3)
    print(3 * c)

    # len(char) -- always 1
    print(len(c))

    # ord(str) -- single char string
    s = "B"
    print(ord(s))

    # ord(char) still works
    print(ord(c))

    # char(str) constructor
    s2 = "Z"
    z = char(s2)
    print(z)


main()
