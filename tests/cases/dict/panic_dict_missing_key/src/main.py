# Subscript access on missing key should panic
def main() -> None:
    d = {"x": 1}
    print(d["missing"])

main()
