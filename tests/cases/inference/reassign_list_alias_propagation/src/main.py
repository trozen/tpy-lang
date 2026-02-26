# List alias propagation: mutation on alias promotes both to list
def main() -> None:
    a = [1, 2, 3]  # tpyc: type(/list/)
    b = a
    b.append(4)
    print(len(a))

main()
