# Pop on missing key without default should panic
def main() -> None:
    d = {"x": 1}
    d.pop("missing")

main()
