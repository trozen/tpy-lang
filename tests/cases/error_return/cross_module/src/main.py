# Cross-module @error_return: call an imported error_return function and handle its error
from errors import NotFound, find

def main() -> None:
    try:
        idx = find([10, 20, 30], 20)
    except NotFound:
        print("not found")
    else:
        print(idx)

    try:
        idx2 = find([10, 20, 30], 99)
    except NotFound:
        print("not found")
    else:
        print(idx2)

main()
