# Dict for-each iterates keys in insertion order
def main() -> None:
    d = {"alpha": 1, "beta": 2, "gamma": 3}
    for k in d:
        print(k, d[k])

main()
