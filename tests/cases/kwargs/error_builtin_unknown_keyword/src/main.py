# sorted() takes key= and reverse= by keyword; any other keyword is reported
# by name.
def main() -> None:
    xs = [3, 1, 2]
    print(sorted(xs, rev=True))  # tpyc: error(/'sorted\(\)' does not support keyword argument 'rev'/)


main()
