# 3-hop alias chain: mutation on c propagates through b to a
def main() -> None:
    a = [1, 2, 3]  # tpyc: type(/list/)
    b = a           # tpyc: type(/list/)
    c = b           # tpyc: type(/list/)
    c.append(4)
    print(len(a))

main()
