# A star import, unlike a from-import, does not take a started submodule
# from the modules being imported.
from tp import *  # tpyc: warning(/'tp.sub' is read while 'tp.sub' is still being imported -- .*AttributeError \(partially initialized module 'tp' has no attribute 'sub'\)/)
