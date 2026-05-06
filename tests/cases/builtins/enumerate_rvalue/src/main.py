# enumerate() with rvalue iterables (temporaries from function calls)
from tpy import Own

def make_words() -> Own[list[str]]:
    return ["hello", "world", "test"]

def main() -> None:
    # rvalue: function call result passed directly
    for i, s in enumerate(make_words()):
        print(i, s)

    # rvalue with start
    for i, s in enumerate(make_words(), 10):
        print(i, s)

    # rvalue: list() from generator expression.
    nums = [1, 2, 3]
    for i, s in enumerate(list(str(x) for x in nums)):
        print(i, s)

main()
