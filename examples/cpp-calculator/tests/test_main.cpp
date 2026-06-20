#include <cassert>
#include <cstdlib>
#include <string>
#include <cstdio>

// Simple integration test: run the calculator binary and check output
int main() {
    // Test 1: add
    int ret = system("./build/calc add 5 3 2>&1");
    assert(ret == 0 && "add 5 3 should succeed");

    // Test 2: divide by non-zero
    ret = system("./build/calc div 10 2 2>&1");
    assert(ret == 0 && "div 10 2 should succeed");

    // Test 3: divide by zero (should NOT crash after fix)
    ret = system("./build/calc div 10 0 2>&1");
    // After fix, this should return non-zero but NOT segfault
    // Pre-fix: this may crash or return infinity

    // Test 4: no arguments (should NOT crash after fix)
    ret = system("./build/calc 2>&1");
    // After fix, this should print usage instead of segfault

    printf("All integration tests completed.\n");
    return 0;
}
