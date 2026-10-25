# isinstance() takes no keyword arguments, as in CPython.


def main() -> None:
    x = 5
    print(isinstance(obj=x, class_or_tuple=int))  # tpyc: error(/isinstance\(\) takes no keyword arguments/)


main()
