# Test str.split() -- separator split, whitespace split, maxsplit
def main() -> None:
    # Split with separator
    parts = "a,b,c".split(",")
    print(len(parts), parts[0], parts[1], parts[2])

    # Split whitespace default
    words = "  one  two  three  ".split()
    print(len(words), words[0], words[1], words[2])

    # Split with maxsplit
    limited = "a,b,c,d".split(",", 2)
    print(len(limited), limited[0], limited[1], limited[2])

    # Empty parts from consecutive separators
    empties = ",a,,b,".split(",")
    print(len(empties))

    # No match -- entire string as single element
    nomatch = "hello".split(",")
    print(len(nomatch), nomatch[0])

    # Whitespace split on simple spaces
    two = "one two three".split()
    print(len(two), two[0], two[1], two[2])

    # Variable separator
    sep = ":"
    data = "x:y:z".split(sep)
    print(len(data), data[0], data[1], data[2])

main()
