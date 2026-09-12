# char type with default parameter value
from tpy import char

def greet(ch: char = 'X') -> None:
    print(ch)

def bracket(text: str, open_ch: char = '(', close_ch: char = ')') -> str:
    return str(open_ch) + text + str(close_ch)

def main() -> None:
    greet()
    greet('A')

    print(bracket("hello"))
    print(bracket("hello", '[', ']'))

main()
