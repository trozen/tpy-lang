# A fixed comprehension above the array_from_index size cap (1024) resolves to
# list -- the N-element pack expansion would be a C++ compile-time cliff. At
# the cap it stays a stack Array.
def main():
    big = [i for i in range(2000)]  # tpyc: type(/list\[int32\]/)
    edge = [i for i in range(1024)]  # tpyc: type(/Array\[int32, 1024\]/)
    print(len(big), big[1999], len(edge), edge[1023])


main()
