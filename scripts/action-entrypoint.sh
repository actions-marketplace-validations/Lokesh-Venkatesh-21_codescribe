#!/bin/sh
set -eu

value() {
  printenv "$1" 2>/dev/null || printf '%s' "$2"
}

POST_COMMENT="$(value INPUT_POST-COMMENT false)"
POST_REVIEW="$(value INPUT_POST-REVIEW false)"
AUTO_APPROVE="$(value INPUT_AUTO-APPROVE false)"
FAIL_ON_RISK="$(value INPUT_FAIL-ON-RISK false)"
RISK_THRESHOLD="$(value INPUT_RISK-THRESHOLD 70)"
LLM_PROVIDER="$(value INPUT_LLM-PROVIDER auto)"
MODEL="$(value INPUT_MODEL '')"
LLM_API_KEY="$(value INPUT_LLM-API-KEY '')"
LLM_API_BASE_URL="$(value INPUT_LLM-API-BASE-URL '')"
INCLUDE="$(value INPUT_INCLUDE '')"
EXCLUDE="$(value INPUT_EXCLUDE '')"
CONFIG_FILE="$(value INPUT_CONFIG-FILE .codescribe.yml)"
OUTPUT_DIR="$(value INPUT_OUTPUT-DIR codescribe-reports)"
WRITE_ARTIFACTS="$(value INPUT_WRITE-ARTIFACTS false)"
ANNOTATE_CODE="$(value INPUT_ANNOTATE-CODE true)"
COMMIT_DOCUMENTATION="$(value INPUT_COMMIT-DOCUMENTATION true)"
DOCUMENTATION_FILE="$(value INPUT_DOCUMENTATION-FILE documentation.md)"
PR_NUMBER="${PR_NUMBER:-$(python -c 'import json, os; print(json.load(open(os.environ["GITHUB_EVENT_PATH"]))["pull_request"]["number"])')}"
BASE_REF="${BASE_REF:-$(python -c 'import json, os; print(json.load(open(os.environ["GITHUB_EVENT_PATH"]))["pull_request"]["base"]["sha"])')}"
HEAD_REF="${HEAD_REF:-$(python -c 'import json, os; print(json.load(open(os.environ["GITHUB_EVENT_PATH"]))["pull_request"]["head"]["sha"])')}"
HEAD_BRANCH="${HEAD_BRANCH:-$(python -c 'import json, os; print(json.load(open(os.environ["GITHUB_EVENT_PATH"]))["pull_request"]["head"]["ref"])')}"
PR_AUTHOR="${PR_AUTHOR:-$(python -c 'import json, os; print(json.load(open(os.environ["GITHUB_EVENT_PATH"]))["pull_request"]["user"]["login"])')}"
PR_URL="${PR_URL:-$(python -c 'import json, os; print(json.load(open(os.environ["GITHUB_EVENT_PATH"]))["pull_request"]["html_url"])')}"

export CODESCRIBE_MODE=github_action
export POST_PR_COMMENT="${POST_COMMENT}"
export AUTO_POST_REVIEWS="${POST_REVIEW}"
export LLM_PROVIDER="${LLM_PROVIDER}"
export GEMINI_API_KEY="${LLM_API_KEY}"
export GENERIC_LLM_API_KEY="${LLM_API_KEY}"
export GENERIC_LLM_API_BASE_URL="${LLM_API_BASE_URL}"

codescribe analyze-pr \
  --repo "${GITHUB_REPOSITORY}" \
  --pr-number "${PR_NUMBER}" \
  --base-ref "${BASE_REF}" \
  --head-ref "${HEAD_REF}" \
  --output-dir "${OUTPUT_DIR}" \
  --post-comment "${POST_COMMENT}" \
  --post-review "${POST_REVIEW}" \
  --auto-approve "${AUTO_APPROVE}" \
  --fail-on-risk "${FAIL_ON_RISK}" \
  --risk-threshold "${RISK_THRESHOLD}" \
  --llm-provider "${LLM_PROVIDER}" \
  --model "${MODEL}" \
  --include "${INCLUDE}" \
  --exclude "${EXCLUDE}" \
  --config-file "${CONFIG_FILE}" \
  --write-artifacts "${WRITE_ARTIFACTS}" \
  --annotate-code "${ANNOTATE_CODE}" \
  --commit-documentation "${COMMIT_DOCUMENTATION}" \
  --documentation-file "${DOCUMENTATION_FILE}" \
  --head-branch "${HEAD_BRANCH}" \
  --pr-author "${PR_AUTHOR}" \
  --pr-url "${PR_URL}"
