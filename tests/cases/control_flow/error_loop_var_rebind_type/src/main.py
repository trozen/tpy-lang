# Rebinding an existing local with a different element type is rejected
# (the C++ slot cannot retype).
def main() -> None:
    x = "hello"
    for x in range(3):  # tpyc: error(/for-loop rebinds existing variable 'x'/)
        pass
    print(x)


main()
