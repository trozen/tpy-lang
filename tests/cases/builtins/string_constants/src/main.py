# Tests for string module constants
import string

def main() -> None:
    print(string.digits)
    print(string.ascii_lowercase)
    print(string.ascii_uppercase)
    print(len(string.ascii_letters))  # 52
    print(len(string.digits))          # 10
    print(len(string.hexdigits))       # 22
    print(len(string.octdigits))       # 8
    print(len(string.whitespace))      # 6
    # Check membership
    print('a' in string.ascii_lowercase)
    print('Z' in string.ascii_uppercase)
    print('5' in string.digits)

main()
