#!/usr/bin/env bash
# ClawdContext OS — Ed25519 Key Generation
# Layer 2: ClawdSign
#
# Generates an Ed25519 keypair for signing SKILL.md bundles.
# Private key stays local. Public key is distributed with the repo.
#
# Usage: ./keygen.sh [output_dir]
# Default output: ./keys/

set -euo pipefail

OUTPUT_DIR="${1:-./keys}"
PRIVATE_KEY="$OUTPUT_DIR/private.key"
PUBLIC_KEY="$OUTPUT_DIR/public.key"

echo "╔════════════════════════════════════════╗"
echo "║   ClawdContext OS — Key Generation     ║"
echo "║   Layer 2: ClawdSign (Ed25519)         ║"
echo "╚════════════════════════════════════════╝"
echo ""

# Check for openssl
if ! command -v openssl &> /dev/null; then
    echo "ERROR: openssl is required but not installed."
    echo "  macOS:  brew install openssl"
    echo "  Linux:  apt install openssl"
    exit 1
fi

# Check openssl supports Ed25519
if ! openssl genpkey -algorithm Ed25519 -help &> /dev/null 2>&1; then
    echo "ERROR: openssl does not support Ed25519."
    echo "       Requires OpenSSL >= 1.1.1"
    echo "       Current: $(openssl version)"
    exit 1
fi

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Check for existing keys
if [[ -f "$PRIVATE_KEY" ]]; then
    echo "WARNING: Private key already exists at $PRIVATE_KEY"
    read -rp "Overwrite? (y/N) " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "Aborted. Existing keys preserved."
        exit 0
    fi
fi

# Generate Ed25519 private key
echo "Generating Ed25519 private key..."
openssl genpkey -algorithm Ed25519 -out "$PRIVATE_KEY"
chmod 600 "$PRIVATE_KEY"

# Extract public key
echo "Extracting public key..."
openssl pkey -in "$PRIVATE_KEY" -pubout -out "$PUBLIC_KEY"
chmod 644 "$PUBLIC_KEY"

echo ""
echo "✓ Keys generated successfully!"
echo ""
echo "  Private key: $PRIVATE_KEY (chmod 600 — keep secret!)"
echo "  Public key:  $PUBLIC_KEY  (distribute with repo)"
echo ""
echo "Next steps:"
echo "  1. Add $PUBLIC_KEY to your repository"
echo "  2. Add $PRIVATE_KEY to .gitignore (NEVER commit!)"
echo "  3. Sign skills: ./verify.sh sign <skill_dir> $PRIVATE_KEY"
echo "  4. Verify:      ./verify.sh verify <skill_dir> $PUBLIC_KEY"
echo ""
echo "WARNING: If you lose the private key, all skills must be re-signed."

# Add private key to .gitignore if not already there
GITIGNORE="$(dirname "$OUTPUT_DIR")/.gitignore"
if [[ -f "$GITIGNORE" ]]; then
    if ! grep -q "private.key" "$GITIGNORE"; then
        echo "*.key" >> "$GITIGNORE"
        echo "Added *.key to .gitignore"
    fi
fi
