# int() takes its value positionally only, as in CPython
# (TypeError: 'x' is an invalid keyword argument for int()). Siblings:
# error_int32_keyword_construction, error_float32_keyword_construction.


def main() -> None:
    print(int(x=5))  # tpyc: error(/No matching overload for 'int' with argument types \(x=int\)/)


main()
