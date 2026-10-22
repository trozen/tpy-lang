# A generator-expression walrus whose target the enclosing function uses is
# refused (BUGS.md#genexpr-walrus-target-stays-in-frame).
def main() -> None:
    last = 0
    # The subject: `last` is read after the generator expression.
    total = sum(x for x in range(6) if (last := x) > 2)  # tpyc: error(/assignment expression in a generator expression cannot yet bind 'last' in the enclosing scope; use a for loop or a list comprehension/)
    print(total, last)


main()
