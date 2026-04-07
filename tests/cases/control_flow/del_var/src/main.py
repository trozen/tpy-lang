# del on local variables: basic unbinding and re-assignment
def main() -> None:
    x = 42
    print(x)
    del x
    x = 100
    print(x)

    # del multiple vars
    a = 1
    b = 2
    print(a, b)
    del a, b
    a = 10
    b = 20
    print(a, b)

main()
