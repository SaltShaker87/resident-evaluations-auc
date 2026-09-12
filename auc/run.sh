#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/backend"
source venv/bin/activate

# Where to listen comes from backend/config.py, which honours AUC_HOST and
# AUC_PORT from the environment. Reading it here rather than repeating the
# defaults means there is one place to change them.
eval "$(python - <<'PYEOF'
import shlex

import config

print(f"AUC_HOST={shlex.quote(config.HOST)}")
print(f"AUC_PORT={shlex.quote(str(config.PORT))}")
PYEOF
)"

exec uvicorn app:app --host "$AUC_HOST" --port "$AUC_PORT"
