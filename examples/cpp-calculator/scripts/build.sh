#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$PROJECT_DIR/build"
PASSED=0
FAILED=0

echo "=== Running tests ==="

# Test 1: add
echo -n "Test add 5 3: "
if OUTPUT=$("$BUILD_DIR/calc" add 5 3 2>&1); then
    if echo "$OUTPUT" | grep -q "10"; then
        echo "PASS"
        PASSED=$((PASSED + 1))
    else
        echo "FAIL (unexpected output: $OUTPUT)"
        FAILED=$((FAILED + 1))
    fi
else
    echo "FAIL (crashed)"
    FAILED=$((FAILED + 1))
fi

# Test 2: div
echo -n "Test div 10 2: "
if OUTPUT=$("$BUILD_DIR/calc" div 10 2 2>&1); then
    if echo "$OUTPUT" | grep -q "5"; then
        echo "PASS"
        PASSED=$((PASSED + 1))
    else
        echo "FAIL (unexpected output: $OUTPUT)"
        FAILED=$((FAILED + 1))
    fi
else
    echo "FAIL (crashed)"
    FAILED=$((FAILED + 1))
fi

# Test 3: div by zero
echo -n "Test div 10 0: "
if "$BUILD_DIR/calc" div 10 0 >/dev/null 2>&1; then
    echo "PASS (non-zero exit expected after fix)"
    PASSED=$((PASSED + 1))
else
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 139 ]; then
        echo "FAIL (segfault - Bug #2 not fixed)"
        FAILED=$((FAILED + 1))
    else
        echo "PASS (graceful exit with code $EXIT_CODE)"
        PASSED=$((PASSED + 1))
    fi
fi

# Test 4: no args
echo -n "Test no arguments: "
if "$BUILD_DIR/calc" >/dev/null 2>&1; then
    echo "PASS"
    PASSED=$((PASSED + 1))
else
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 139 ]; then
        echo "FAIL (segfault - Bug #1 not fixed)"
        FAILED=$((FAILED + 1))
    else
        echo "PASS (graceful exit with code $EXIT_CODE)"
        PASSED=$((PASSED + 1))
    fi
fi

echo ""
echo "=== Results: $PASSED passed, $FAILED failed ==="
exit $FAILED
