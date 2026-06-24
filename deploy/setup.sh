#!/usr/bin/env bash
# Run once on the VPS as root to set up the GitHub Actions runner and app directory.
set -euo pipefail

REPO="lavavc/automated-infra"
RUNNER_USER="github-runner"
RUNNER_DIR="/opt/actions-runner"

# Check https://github.com/actions/runner/releases for the latest version
RUNNER_VERSION="2.321.0"

echo "=== Setting up /opt/repo ==="
if [ ! -d /opt/repo ]; then
  git clone "https://github.com/${REPO}.git" /opt/repo
fi
mkdir -p /opt/repo/data
chown -R "$RUNNER_USER:$RUNNER_USER" /opt/repo
echo "/opt/repo is owned by $RUNNER_USER."

echo ""
echo "=== Creating runner user ==="
useradd -m -s /bin/bash "$RUNNER_USER" || echo "User $RUNNER_USER already exists, skipping."
usermod -aG docker "$RUNNER_USER"
echo "User $RUNNER_USER added to docker group."

if [ -f "${RUNNER_DIR}/.runner" ]; then
  echo ""
  echo "=== GitHub Actions runner already configured, skipping ==="
else
  echo ""
  echo "=== Downloading GitHub Actions runner v${RUNNER_VERSION} ==="
  mkdir -p "$RUNNER_DIR"
  cd "$RUNNER_DIR"

  curl -sSL -o runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
  tar xzf runner.tar.gz
  rm runner.tar.gz
  chown -R "$RUNNER_USER:$RUNNER_USER" "$RUNNER_DIR"

  echo ""
  echo "=== Runner is ready to configure ==="
  echo ""
  echo "1. Get your registration token at:"
  echo "   https://github.com/${REPO}/settings/actions/runners/new"
  echo ""
  read -rp "Paste the token here: " RUNNER_TOKEN

  echo ""
  echo "=== Registering runner ==="
  su - "$RUNNER_USER" -c "
    cd $RUNNER_DIR
    ./config.sh \
      --url https://github.com/${REPO} \
      --token ${RUNNER_TOKEN} \
      --unattended \
      --name vps-runner
  "

  echo ""
  echo "=== Installing systemd service ==="
  cd "$RUNNER_DIR"
  ./svc.sh install "$RUNNER_USER"
  ./svc.sh start
  echo "Runner service started."
fi

echo ""
echo "=== Installing Caddy ==="
if ! command -v caddy &>/dev/null; then
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update
  apt-get install -y caddy
else
  echo "Caddy already installed, skipping."
fi

echo ""
echo "=== Opening firewall for HTTP/HTTPS ==="
if command -v ufw &>/dev/null; then
  ufw allow 80/tcp
  ufw allow 443/tcp
  echo "Allowed 80/tcp and 443/tcp (enable ufw separately if it is not already active)."
else
  echo "ufw not installed; ensure ports 80 and 443 are open at your provider firewall."
fi

echo ""
echo "=== Installing Caddyfile ==="
# Hostname and the Quidax webhook source-IP allowlist live in deploy/Caddyfile.
cp /opt/repo/deploy/Caddyfile /etc/caddy/Caddyfile
systemctl reload caddy || systemctl restart caddy
echo "Caddy serving the dashboard with automatic Let's Encrypt TLS."

echo ""
echo "=== Done ==="
echo ""
echo "Remaining steps:"
echo "  1. At your DNS provider, create an A record for the dashboard hostname"
echo "     (cngn.lavavc.io) pointing to this server's public IP."
echo "  2. Copy your .env to the server:"
echo "       cat .env | ssh root@<server-ip> \"cat > /opt/repo/.env\""
echo "  3. Push to main to trigger the first deployment."
echo ""
echo "The dashboard is public and read-only. Mutating endpoints require"
echo "ENGINE_API_TOKEN; the Quidax webhook is locked by source IP in deploy/Caddyfile."
