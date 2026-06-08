# @unsafe_send and @nosend on the same target are contradictory.
from tpy import unsafe_send, nosend

@unsafe_send
@nosend
class A:  # tpyc: error(/contradictory Send-override decorators/)
    pass
