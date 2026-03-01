# Char type with default parameter value
from tpy import Char

def greet(ch: Char = 'X') -> None:
    print(ch)

def bracket(text: str, open_ch: Char = '(', close_ch: Char = ')') -> str:
    return str(open_ch) + text + str(close_ch)

def main() -> None:
    greet()
    greet('A')

    print(bracket("hello"))
    print(bracket("hello", '[', ']'))

main()
