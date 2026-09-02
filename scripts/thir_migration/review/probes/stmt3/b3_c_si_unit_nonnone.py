from tpy import Int32
def main() -> None:
    d: dict[str, None] = {}
    e: dict[str, None] = {}
    d['a'] = e['b']
    print(len(d))
main()
