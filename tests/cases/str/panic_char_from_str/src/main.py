# char(str) panics when string length != 1.
from tpy import char


def main():
    s = "hello"
    c = char(s)
    print(c)


main()
