# Invalid Python (CPython raises SyntaxError): a walrus in a comprehension
# iterable is reported with Python's own message.
def main() -> None:
    # The subject: the walrus sits in the iterable of the generator expression.
    print(sum(x for x in [(y := 2)]))  # tpyc: error(/assignment expression cannot be used in a comprehension iterable expression/)


main()
