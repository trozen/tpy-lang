from typing import Sequence
from tpy import Int32, Char

def first_char(s: Sequence[Char]) -> Char:
    return s[0]

def count_chars(s: Sequence[Char]) -> Int32:
    return len(s)

def main() -> None:
    text: str = "hello"

    # str conforms to Sequence[Char]
    print(first_char(text))    # h
    print(count_chars(text))   # 5

    # Direct indexing on str
    print(text[0])             # h
    print(text[-1])            # o
    print(text[2])             # l

main()
