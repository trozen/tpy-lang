from tpy import int32, readonly
def ident(t: list[int32]) -> readonly[list[int32]]:
    return t
def main() -> None:
    t = [1, 2]
    items = ident(t)
    print(len(items))
main()
