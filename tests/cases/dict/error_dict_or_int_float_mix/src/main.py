# Two dict literals whose values mix int and float share no type: CPython
# keeps the dict it picks (docs/LANGUAGE_FEATURES.md, numeric tower).


def main() -> None:
    # The literals are concrete dicts, so the join compares their values.
    v = {"a": 1} or {"b": 2.5}  # tpyc: error(/this `or` mixes int and float elements, and CPython keeps whichever value it picks; write 1\.0 instead of 1, or annotate the target as dict\[str, float\]$/)
    print(v)


main()
