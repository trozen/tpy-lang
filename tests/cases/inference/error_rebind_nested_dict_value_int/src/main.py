# A dict-of-dicts local rebound to {"a": {}, "b": {"k": 2}}: only the local's
# type makes the empty value float, so the rebind refuses and names the local.


def rebind(d: dict[str, dict[str, float]]) -> None:
    e = d
    e = {"a": {}, "b": {"k": 2}}  # tpyc: error(/'e' is bound to float elements at line 6 and to int32 elements here/)
    print(e)


rebind({"a": {"k": 1.5}})
