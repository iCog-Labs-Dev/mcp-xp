#!/usr/bin/env bash

# Run all unit tests under tests with pytest

set -e  # Exit immediately if any command fails
set -o pipefail
set -u  # Treat unset variables as errors

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_TEST_DIR="$PROJECT_ROOT/tests"

echo "Running unit tests in: $UNIT_TEST_DIR"
echo "============================================================"

echo "Starting unit tests in $UNIT_TEST_DIR...."
# Run pytest with useful options, tee output for parsing while displaying in logs
uv run pytest "$UNIT_TEST_DIR" \
  -v \
  -s \
  --disable-warnings \
  --maxfail=3 \
  --asyncio-mode=auto \
  --cov=. \
  --cov-report=html:htmlcov \
  --cov-report=xml \
  --cov-report=term-missing \
  --cov-fail-under=50 | tee pytest_output.txt

RESULT=$?

# Parse total coverage percentage from the output (assumes standard pytest-cov format)
COVERAGE_PERCENT=$(grep '^TOTAL' pytest_output.txt | awk '{print $4}' | sed 's/%//')

# Append to GitHub job summary if running in GitHub Actions
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    echo "### Test Coverage Summary" >> "$GITHUB_STEP_SUMMARY"
    echo "Total Coverage: ${COVERAGE_PERCENT}%" >> "$GITHUB_STEP_SUMMARY"
fi

# Clean up temporary file
rm -f pytest_output.txt

echo "============================================================"
if [ $RESULT -eq 0 ]; then
    echo " All unit tests passed successfully!!!!"
else
    echo " Some unit tests failed. Check logs above."
fi

exit $RESULT
