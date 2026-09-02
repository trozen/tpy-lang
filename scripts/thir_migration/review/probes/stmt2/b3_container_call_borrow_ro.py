from tpy import Int32, readonly
def ident(t: list[Int32]) -> readonly[list[Int32]]:
    return t
def main() -> None:
    t = [1, 2]
    items = ident(t)
    print(len(items))
main()
