# Different-size list reassignment widens to list
def main() -> None:
    x = [1, 2, 3]  # tpyc: type(/list/)
    x = [4, 5]
    print(len(x))

main()
