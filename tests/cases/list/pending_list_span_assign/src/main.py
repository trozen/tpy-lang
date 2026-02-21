from tpy import Int32, Span

def main() -> None:
    data = [1, 2, 3]
    s: Span[Int32] = data
    print(s[0])
    print(s[2])

main()
