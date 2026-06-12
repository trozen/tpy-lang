# Returning an empty dict from a function with no return annotation is rejected:
# the return type is None, so the pending dict has no use-site type to resolve
# from (guards that VOID return context does not silently resolve it).
def f():
    d = {}
    return d  # tpyc: error(/Type mismatch in return value: expected None/)


f()
