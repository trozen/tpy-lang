from tpy import int32
def main() -> None:
    xss = [[1, 2, 3], [4]]
    xss[0][1:3] = [9, 9]
    print(xss)
main()
