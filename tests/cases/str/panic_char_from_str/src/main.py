# Char(str) panics when string length != 1.
from tpy import Char


def main():
    s = "hello"
    c = Char(s)
    print(c)


main()
