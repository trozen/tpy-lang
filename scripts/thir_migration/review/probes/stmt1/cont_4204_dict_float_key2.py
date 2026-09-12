from tpy import int32
def main() -> None:
    d = {1.5: 2}
    other = {2.5: 3}
    d = other
    print(len(d))
main()
