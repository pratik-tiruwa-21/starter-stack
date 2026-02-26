#!/usr/bin/env bash
# ClawdContext OS — Skill Signature Verification
# Layer 2: ClawdSign
#
# Signs and verifies SKILL.md bundles using Ed25519.
# Signature is stored in YAML frontmatter of SKILL.md.
#
# Usage:
#   ./verify.sh sign   <skill_dir> <private_key>   — Sign a skill
#   ./verify.sh verify <skill_dir> <public_key>    — Verify a skill
#   ./verify.sh check  <skills_dir> <public_key>   — Check all skills

set -euo pipefail

COMMAND="${1:-help}"
TARGET="${2:-}"
KEY="${3:-}"

echo "╔════════════════════════════════════════╗"
echo "║   ClawdContext OS — Skill Verifier     ║"
echo "║   Layer 2: ClawdSign (Ed25519)         ║"
echo "╚════════════════════════════════════════╝"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

sign_skill() {
    local skill_dir="$1"
    local private_key="$2"
    local skill_file="$skill_dir/SKILL.md"

    if [[ ! -f "$skill_file" ]]; then
        echo -e "${RED}ERROR: $skill_file not found${NC}"
        return 1
    fi

    if [[ ! -f "$private_key" ]]; then
        echo -e "${RED}ERROR: Private key not found: $private_key${NC}"
        return 1
    fi

    # Extract content after frontmatter for signing
    # (Sign the actual content, not the metadata)
    local content
    content=$(sed -n '/^---$/,/^---$/!p' "$skill_file" | tail -n +1)

    # Generate signature
    local sig
    sig=$(echo -n "$content" | openssl pkeyutl -sign -inkey "$private_key" | base64 -w 0 2>/dev/null || echo -n "$content" | openssl pkeyutl -sign -inkey "$private_key" | base64 2>/dev/null)

    local signer
    signer=$(openssl pkey -in "$private_key" -pubout 2>/dev/null | openssl dgst -sha256 | awk '{print $2}' | head -c 16)

    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    # Update frontmatter with signature
    if command -v sed &>/dev/null; then
        sed -i.bak "s|^signature:.*|signature: \"$sig\"|" "$skill_file"
        sed -i.bak "s|^signed_by:.*|signed_by: \"$signer\"|" "$skill_file"
        sed -i.bak "s|^signed_at:.*|signed_at: \"$timestamp\"|" "$skill_file"
        rm -f "$skill_file.bak"
    fi

    echo -e "${GREEN}✓ Signed: $skill_file${NC}"
    echo "  Signer:    $signer"
    echo "  Timestamp: $timestamp"
    echo "  Signature: ${sig:0:32}..."
}

verify_skill() {
    local skill_dir="$1"
    local public_key="$2"
    local skill_file="$skill_dir/SKILL.md"

    if [[ ! -f "$skill_file" ]]; then
        echo -e "${RED}✗ MISSING: $skill_file${NC}"
        return 1
    fi

    if [[ ! -f "$public_key" ]]; then
        echo -e "${RED}ERROR: Public key not found: $public_key${NC}"
        return 1
    fi

    # Extract signature from frontmatter
    local sig
    sig=$(grep '^signature:' "$skill_file" | sed 's/signature: *"\(.*\)"/\1/')

    # Check for unsigned or forged
    if [[ -z "$sig" || "$sig" == *"UNSIGNED"* ]]; then
        echo -e "${YELLOW}⚠ UNSIGNED: $skill_file${NC}"
        return 1
    fi

    if [[ "$sig" == *"FORGED"* ]]; then
        echo -e "${RED}✗ FORGED SIGNATURE: $skill_file${NC}"
        return 1
    fi

    # Extract content for verification
    local content
    content=$(sed -n '/^---$/,/^---$/!p' "$skill_file" | tail -n +1)

    # Verify signature
    local result
    if echo -n "$content" | openssl pkeyutl -verify -pubin -inkey "$public_key" -sigfile <(echo "$sig" | base64 -d 2>/dev/null || echo "$sig" | base64 -D 2>/dev/null) 2>/dev/null; then
        echo -e "${GREEN}✓ VALID: $skill_file${NC}"
        return 0
    else
        echo -e "${RED}✗ INVALID SIGNATURE: $skill_file${NC}"
        return 1
    fi
}

check_all() {
    local skills_dir="$1"
    local public_key="$2"
    local total=0
    local valid=0
    local invalid=0
    local unsigned=0

    echo ""
    echo "Checking all skills in $skills_dir..."
    echo "──────────────────────────────────────"

    for skill_dir in "$skills_dir"/*/; do
        if [[ -f "$skill_dir/SKILL.md" ]]; then
            total=$((total + 1))
            if verify_skill "$skill_dir" "$public_key"; then
                valid=$((valid + 1))
            else
                # Check if unsigned vs invalid
                local sig
                sig=$(grep '^signature:' "$skill_dir/SKILL.md" | sed 's/signature: *"\(.*\)"/\1/')
                if [[ -z "$sig" || "$sig" == *"UNSIGNED"* ]]; then
                    unsigned=$((unsigned + 1))
                else
                    invalid=$((invalid + 1))
                fi
            fi
        fi
    done

    echo ""
    echo "──────────────────────────────────────"
    echo "Results: $total skills checked"
    echo -e "  ${GREEN}Valid:    $valid${NC}"
    echo -e "  ${YELLOW}Unsigned: $unsigned${NC}"
    echo -e "  ${RED}Invalid:  $invalid${NC}"

    if [[ $invalid -gt 0 ]]; then
        echo ""
        echo -e "${RED}⚠ SECURITY ALERT: $invalid skill(s) have invalid signatures!${NC}"
        return 1
    fi

    if [[ $unsigned -gt 0 ]]; then
        echo ""
        echo -e "${YELLOW}⚠ WARNING: $unsigned skill(s) are unsigned.${NC}"
        echo "  Run: ./verify.sh sign <skill_dir> <private_key>"
    fi
}

case "$COMMAND" in
    sign)
        [[ -z "$TARGET" || -z "$KEY" ]] && { echo "Usage: $0 sign <skill_dir> <private_key>"; exit 1; }
        sign_skill "$TARGET" "$KEY"
        ;;
    verify)
        [[ -z "$TARGET" || -z "$KEY" ]] && { echo "Usage: $0 verify <skill_dir> <public_key>"; exit 1; }
        verify_skill "$TARGET" "$KEY"
        ;;
    check)
        [[ -z "$TARGET" || -z "$KEY" ]] && { echo "Usage: $0 check <skills_dir> <public_key>"; exit 1; }
        check_all "$TARGET" "$KEY"
        ;;
    *)
        echo ""
        echo "Usage:"
        echo "  $0 sign   <skill_dir>  <private_key>  — Sign a SKILL.md"
        echo "  $0 verify <skill_dir>  <public_key>   — Verify a SKILL.md"
        echo "  $0 check  <skills_dir> <public_key>   — Check all skills"
        echo ""
        echo "Examples:"
        echo "  $0 sign   agent/skills/web-search keys/private.key"
        echo "  $0 verify agent/skills/web-search keys/public.key"
        echo "  $0 check  agent/skills keys/public.key"
        ;;
esac
