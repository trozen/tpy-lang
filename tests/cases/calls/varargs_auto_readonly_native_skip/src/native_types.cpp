#include "native_types.hpp"

int32_t __user_sum_ints(::tpy::varargs<int32_t> xs) {
    int32_t total = 0;
    for (int32_t i = 0; i < xs.size(); ++i) {
        total += xs[i];
    }
    return total;
}
