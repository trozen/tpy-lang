# list[str].append with local string variable (string_view -> string conversion)
def main() -> None:
    items: list[str] = []
    s = "hello"
    items.append(s)
    items.append("literal")
    items.append(s + " world")
    print(items)

main()
