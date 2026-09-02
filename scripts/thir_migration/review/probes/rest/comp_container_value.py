from tpy import Int32
def main() -> None:
    xs = [1, 2]
    ys = [xs for i in range(3)]
    print(len(ys))
main()
