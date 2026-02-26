# Multiple same-size cross-variable reassignments promote all to list
def main() -> None:
    a = [1, 2, 3]  # tpyc: type(/list/)
    b = [4, 5, 6]  # tpyc: type(/list/)
    c = [7, 8, 9]  # tpyc: type(/list/)
    a = b
    a = c
    a.append(10)
    print(len(c))
main()
