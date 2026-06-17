#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../../../../.." && pwd)"

if [ ! -d "$PROJECT_ROOT/src/cascade" ]; then
	PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
fi

PYTHONPATH="$PROJECT_ROOT/src" "$PROJECT_ROOT/PaperEvaluation/venv-cascade/bin/python" -m cascade.CLI run -i "./repository" -o "." -c "./dataset_config.json" -ana debug:3
