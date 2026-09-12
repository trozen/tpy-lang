from tpy import int32, Span

def main() -> None:
    data = [1, 2, 3]
    s: Span[int32] = data
    print(s[0])
    print(s[2])

main()
