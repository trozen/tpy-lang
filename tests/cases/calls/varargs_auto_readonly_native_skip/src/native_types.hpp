#pragma once
#include <cstdint>
#include <tpy/varargs.hpp>

int32_t __user_sum_ints(::tpy::varargs<int32_t> xs);
