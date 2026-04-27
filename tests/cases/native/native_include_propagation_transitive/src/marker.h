// Empty. Present so the test harness adds this src/ to the include path
// (find_extra_include_dirs in conftest.py only triggers on .hpp/.h here).
// Using .h instead of .hpp avoids being force-included via -include, which
// would mask the propagation bug under test (force-included headers make
// every native type visible regardless of the # tpy: include() chain).
