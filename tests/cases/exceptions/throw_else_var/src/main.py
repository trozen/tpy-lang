# else body references variable declared in try body (hoisted)
from tpy import Int32

def risky(x: Int32) -> Int32:
    if x < 0:
        raise ValueError("negative")
    return x * 2

def main() -> None:
    try:
        result = risky(5)
    except ValueError:
        print("caught")
    else:
        print(result)

main()
