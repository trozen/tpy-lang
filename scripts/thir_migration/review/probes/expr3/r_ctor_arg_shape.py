from tpy import Int32
class MyErr(Exception):
    xs: list[Int32]
    def __init__(self, xs: list[Int32]) -> None:
        self.xs = xs
def boom() -> None:
    raise MyErr([i for i in range(3)])
def main() -> None:
    try:
        boom()
    except MyErr as e:
        print(len(e.xs))
main()
