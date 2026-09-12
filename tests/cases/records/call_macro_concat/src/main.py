# Test call-site macro with *args, sep= and quote_str= kwargs
from strutil import concat
from tpy import int32

def main() -> None:
    s = concat("hello", "world")
    print(s)
    x: int32 = 42
    s2 = concat("x", x, sep="=")
    print(s2)
    s3 = concat("a", "b", "c", sep=", ")
    print(s3)
    s4 = concat("hello", "world", sep=", ", quote_str=True)
    print(s4)

main()
