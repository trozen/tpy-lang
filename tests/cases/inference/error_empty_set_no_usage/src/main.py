# Empty set with no usage that could infer element type
def main() -> None:
    s = set()  # tpyc: error(/Cannot infer element type for set/)
    print(s)

main()
