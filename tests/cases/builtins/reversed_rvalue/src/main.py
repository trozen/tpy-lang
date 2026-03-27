# reversed() with rvalue sequences (temporaries from function calls)
from tpy import Own

def make_nums() -> Own[list[int]]:
    return [3, 1, 4, 1, 5]

def main() -> None:
    # rvalue: function call result passed directly
    for x in reversed(make_nums()):
        print(x)

    # rvalue: sorted() returns Own[list[T]]
    for x in reversed(sorted(make_nums())):
        print(x)

main()
