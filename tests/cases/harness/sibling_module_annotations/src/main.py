# Comp-only `# tpyc:` annotations in sibling modules are validated against
# each sibling's own facts, not only the entry module's.
import part_a
import part_b


def main() -> None:
    t = "entry"  # tpyc: type(StrView)
    print("main:", t)
    part_a.main()
    part_b.main()


main()
