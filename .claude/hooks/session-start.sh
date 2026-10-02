#!/bin/bash
# Installs what the test suite needs in a Claude Code cloud session.
set -euo pipefail
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
python3 -c 'import markdown, yaml' 2>/dev/null && exit 0
pip install --quiet --root-user-action=ignore 'markdown>=3.7' pyyaml
