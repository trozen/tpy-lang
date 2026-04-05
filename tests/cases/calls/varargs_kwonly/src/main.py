# *args combined with keyword-only params
from tpy import Int32

def f(*args: Int32, sep: str = ", ") -> str:
    result = ""
    for i in range(len(args)):
        if i > 0:
            result += sep
        result += str(args[i])
    return result

def main() -> None:
    print(f(1, 2, 3))
    print(f(1, 2, 3, sep=" + "))
    print(f())

main()
