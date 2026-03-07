# Error: nested generators in set comprehension

def main() -> None:
    s = {x + y for x in range(3) for y in range(3)}  # tpyc: error(/Nested/)

main()
