# A `.items()` head is a CALL, so nothing proves the dict it came from is
# non-empty and the names the loop binds are not assigned after it -- the
# tuple-unpack targets included. `dict/dict_items_postloop` pins the accepting
# side with a dict-literal local at the head.
def main() -> None:
    d = {"a": [1, 2, 3], "b": [4, 5]}
    d.clear()
    for k, v in d.items():
        pass
    print(k, len(v))  # tpyc: error(/variable 'k' may not be assigned at this point/)


main()
