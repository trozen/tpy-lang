from tpy import int32, copy
def main() -> None:
    xs = [1, 2]
    ys = copy(xs)
    ys.append(3)
    print(len(xs), len(ys))
main()
