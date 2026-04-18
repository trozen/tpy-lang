/**
 * TurboPython Runtime - Random Number Generation
 *
 * Mersenne Twister (mt19937) backed random functions matching Python's random module.
 * State is held in a function-local static so it persists across calls without
 * requiring a global object with static-init ordering concerns.
 */

#pragma once

#include <algorithm>
#include <cstdint>
#include <random>
#include <vector>

#include "core.hpp"

namespace tpy {

inline std::mt19937& random_engine() {
    static std::mt19937 engine{std::random_device{}()};
    return engine;
}

inline double random_random() {
    std::uniform_real_distribution<double> dist(0.0, 1.0);
    return dist(random_engine());
}

inline void random_seed(int32_t n) {
    random_engine().seed(static_cast<uint32_t>(n));
}

inline int32_t random_randint(int32_t a, int32_t b) {
    if (a > b) tpy_panic("random.randint(): a must be <= b");
    std::uniform_int_distribution<int32_t> dist(a, b);
    return dist(random_engine());
}

inline double random_uniform(double a, double b) {
    std::uniform_real_distribution<double> dist(a, b);
    return dist(random_engine());
}

inline double random_gauss(double mu, double sigma) {
    std::normal_distribution<double> dist(mu, sigma);
    return dist(random_engine());
}

inline double random_expovariate(double lambd) {
    std::exponential_distribution<double> dist(lambd);
    return dist(random_engine());
}

template<typename T>
inline void random_shuffle(std::vector<T>& lst) {
    std::shuffle(lst.begin(), lst.end(), random_engine());
}

} // namespace tpy
