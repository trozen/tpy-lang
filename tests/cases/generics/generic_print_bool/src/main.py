# Generic function print should display bool as True/False, not 1/0
def show[T](x: T) -> None:
    print(x)

def main() -> None:
    show(True)
    show(False)
    show(42)
    show("hello")

main()
