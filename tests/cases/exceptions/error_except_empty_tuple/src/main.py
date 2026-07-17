# Error: `except ():` catches nothing. CPython accepts it as a no-op clause;
# TPy rejects it rather than silently compiling a handler that can never run.
def main() -> None:
    try:
        print("x")
    except ():  # tpyc: error(/catches nothing/)
        print("never")


main()
