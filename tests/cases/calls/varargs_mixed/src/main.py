# *args with fixed positional params before it
def join_parts(sep: str, *parts: str) -> str:
    result = ""
    for i in range(len(parts)):
        if i > 0:
            result += sep
        result += parts[i]
    return result

def main() -> None:
    print(join_parts(", ", "a", "b", "c"))
    print(join_parts("-"))
    print(join_parts(" and ", "x", "y"))

main()
