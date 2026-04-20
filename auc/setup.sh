#!/bin/bash
# ============================================================
# AUC — Assessments Under Curve
# One-time setup script
# ============================================================

set -e

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║   AUC — Assessments Under Curve      ║"
echo "  ║   Setup Script                        ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# Get the directory where this script lives
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ---- Step 1: Check prerequisites ----
echo "[1/5] Checking prerequisites..."

if ! command -v python3 &> /dev/null; then
    echo "  ✗ Python 3 not found. Please install Python 3.10+."
    exit 1
fi
echo "  ✓ Python 3 found: $(python3 --version)"

if ! command -v node &> /dev/null; then
    echo "  ✗ Node.js not found. Please install Node.js 18+."
    echo "    You can install it with: sudo apt install nodejs npm"
    exit 1
fi
echo "  ✓ Node.js found: $(node --version)"

if ! command -v ollama &> /dev/null; then
    echo "  ⚠ Ollama not found. The AI summary feature won't work until you install it."
    echo "    Install from: https://ollama.ai"
else
    echo "  ✓ Ollama found"
fi

# ---- Step 2: Set up Python backend ----
echo ""
echo "[2/5] Setting up Python backend..."

cd "$SCRIPT_DIR/backend"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "  ✓ Created Python virtual environment"
fi

# Install dependencies
source venv/bin/activate
pip install -q -r requirements.txt
echo "  ✓ Python dependencies installed"
deactivate

# ---- Step 3: Build frontend ----
echo ""
echo "[3/5] Building frontend..."

cd "$SCRIPT_DIR/frontend"
npm install --silent 2>/dev/null
npm run build
echo "  ✓ Frontend built"

# ---- Step 4: Create data directory ----
echo ""
echo "[4/5] Setting up data directory..."

mkdir -p "$SCRIPT_DIR/data/photos"
echo "  ✓ Data directory ready"

# ---- Step 5: Create startup script and systemd service ----
echo ""
echo "[5/5] Configuring auto-start..."

# Create the run script
cat > "$SCRIPT_DIR/run.sh" << 'RUNEOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/backend"
source venv/bin/activate
exec uvicorn app:app --host 0.0.0.0 --port 3000
RUNEOF
chmod +x "$SCRIPT_DIR/run.sh"
echo "  ✓ Created run.sh"

# Create systemd service
SERVICE_FILE="$HOME/.config/systemd/user/auc.service"
mkdir -p "$(dirname "$SERVICE_FILE")"

cat > "$SERVICE_FILE" << EOF
[Unit]
Description=AUC — Assessments Under Curve
After=network.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR/backend
ExecStart=$SCRIPT_DIR/backend/venv/bin/uvicorn app:app --host 0.0.0.0 --port 3000
Restart=on-failure
RestartSec=5
Environment=OLLAMA_URL=http://localhost:11434
Environment=OLLAMA_MODEL=qwen3:8b

[Install]
WantedBy=default.target
EOF

# Enable the service
systemctl --user daemon-reload
systemctl --user enable auc.service
systemctl --user start auc.service

echo "  ✓ Auto-start configured"

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║   Setup complete!                     ║"
echo "  ║                                       ║"
echo "  ║   AUC is now running at:              ║"
echo "  ║   http://localhost:3000               ║"
echo "  ║                                       ║"
echo "  ║   It will start automatically         ║"
echo "  ║   when your machine boots.            ║"
echo "  ║                                       ║"
echo "  ║   To stop:  systemctl --user stop auc ║"
echo "  ║   To start: systemctl --user start auc║"
echo "  ║   Logs:     journalctl --user -u auc  ║"
echo "  ╚══════════════════════════════════════╝"
echo ""
