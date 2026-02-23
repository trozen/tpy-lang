# Error: __name__ is a Final constant, cannot reassign
__name__ = "custom"  # tpyc: error(/Cannot reassign Final/)
print(__name__)
