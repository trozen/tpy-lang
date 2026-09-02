from tpy import Int32
def main() -> None:
    xs = [1, 2]
    ys = [3, 4]
    zs = [a + b for a, b in zip(xs, ys)]
    print(zs)
main()
