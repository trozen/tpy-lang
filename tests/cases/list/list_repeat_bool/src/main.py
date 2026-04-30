"""Regression: [False] * n must not dangle on vector<bool>'s proxy reference."""

def main() -> None:
    falses: list[bool] = [False] * 5
    print(len(falses))
    print(falses[0])
    print(falses[4])

    trues: list[bool] = [True] * 3
    print(len(trues))
    print(trues[0])

    mixed: list[bool] = [True, False] * 2
    print(len(mixed))
    print(mixed[0])
    print(mixed[1])
    print(mixed[2])
    print(mixed[3])

    empty: list[bool] = [True] * 0
    print(len(empty))

    neg: list[bool] = [True] * (-2)
    print(len(neg))

    n = 4
    dynamic: list[bool] = [False] * n
    print(len(dynamic))
    print(dynamic[3])

    # Direct iteration over a materialized list[bool] (vector<bool>'s own
    # iterator, distinct from repeat_range's) -- guard against future
    # for-loop lowering accidentally binding bool& to the proxy.
    for x in mixed:
        print(x)

main()
