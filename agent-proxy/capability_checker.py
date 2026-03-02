"""
AgentProxy — Capability Checker

Evaluates per-skill capability grants against OPA/Rego policies.
This is the first gate in the proxy pipeline.

Consumes: security/policies/capabilities.rego
"""

import subprocess
import json
import os
from dataclasses import dataclass
from typing import Optional
from pathlib import Path


@dataclass
class CapabilityGrant:
    """A single capability granted to a skill."""
    type: str          # file_read, file_write, net, exec
    scope: str         # glob pattern or URL pattern
    granted: bool = True


@dataclass  
class SkillCapabilities:
    """Capabilities extracted from a SKILL.md frontmatter."""
    skill_name: str
    capabilities: list[CapabilityGrant]
    signed: bool = False
    token_budget: int = 0
    rate_limit: int = 0


def parse_skill_frontmatter(skill_path: str) -> Optional[SkillCapabilities]:
    """
    Parse YAML frontmatter from a SKILL.md file to extract capabilities.
    
    Expected format:
    ---
    capabilities:
      - file_read: workspace
      - file_write: output/
      - net: duckduckgo.com
    token_budget: 5000
    rate_limit: 10
    signature: ed25519:abc123...
    ---
    """
    try:
        with open(skill_path, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        return None
    
    # Simple YAML frontmatter parser (avoids PyYAML dependency)
    if not content.startswith('---'):
        return None
    
    end = content.find('---', 3)
    if end == -1:
        return None
    
    frontmatter = content[3:end].strip()
    skill_name = Path(skill_path).parent.name
    
    capabilities = []
    token_budget = 0
    rate_limit = 0
    signed = False
    
    in_capabilities = False
    for line in frontmatter.split('\n'):
        line = line.strip()
        
        if line.startswith('capabilities:'):
            in_capabilities = True
            continue
        
        if in_capabilities and line.startswith('- '):
            cap_str = line[2:].strip()
            # Parse "type: scope" or "type:scope"
            if ':' in cap_str:
                parts = cap_str.split(':', 1)
                cap_type = parts[0].strip()
                cap_scope = parts[1].strip()
                capabilities.append(CapabilityGrant(type=cap_type, scope=cap_scope))
            continue
        
        if in_capabilities and not line.startswith('- '):
            in_capabilities = False
        
        if line.startswith('token_budget:'):
            try:
                token_budget = int(line.split(':')[1].strip())
            except ValueError:
                pass
        
        if line.startswith('rate_limit:'):
            try:
                rate_limit = int(line.split(':')[1].strip())
            except ValueError:
                pass
        
        if line.startswith('signature:'):
            sig = line.split(':', 1)[1].strip()
            signed = sig.startswith('ed25519:') and len(sig) > 20
    
    return SkillCapabilities(
        skill_name=skill_name,
        capabilities=capabilities,
        signed=signed,
        token_budget=token_budget,
        rate_limit=rate_limit,
    )


def check_capability_opa(
    skill_name: str,
    action: str,
    target: str,
    policy_path: str = "security/policies/capabilities.rego",
) -> dict:
    """
    Evaluate a capability check against OPA.
    
    Requires `opa` binary in PATH.
    Falls back to deny-all if OPA is not available.
    """
    input_data = {
        "skill": skill_name,
        "action": action,
        "target": target,
    }
    
    try:
        result = subprocess.run(
            [
                "opa", "eval",
                "--data", policy_path,
                "--input", "-",
                "--format", "json",
                "data.capabilities.allow",
            ],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
            timeout=5,
        )
        
        if result.returncode == 0:
            output = json.loads(result.stdout)
            # Extract the result
            results = output.get("result", [])
            if results:
                return {
                    "allowed": results[0].get("expressions", [{}])[0].get("value", False),
                    "source": "opa",
                }
        
        return {"allowed": False, "source": "opa-error", "error": result.stderr}
    
    except FileNotFoundError:
        return {"allowed": False, "source": "opa-not-found"}
    except subprocess.TimeoutExpired:
        return {"allowed": False, "source": "opa-timeout"}
    except Exception as e:
        return {"allowed": False, "source": "error", "error": str(e)}


# ── CLI Usage ───────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    
    print("AgentProxy — Capability Checker")
    print("")
    
    # Parse all skills
    skills_dir = "agent/skills"
    if os.path.isdir(skills_dir):
        for skill_dir in sorted(os.listdir(skills_dir)):
            skill_path = os.path.join(skills_dir, skill_dir, "SKILL.md")
            if os.path.isfile(skill_path):
                skill = parse_skill_frontmatter(skill_path)
                if skill:
                    status = "✓ signed" if skill.signed else "✗ unsigned"
                    print(f"  {skill.skill_name}:")
                    print(f"    Signed: {status}")
                    print(f"    Budget: {skill.token_budget} tokens")
                    print(f"    Rate:   {skill.rate_limit} req/min")
                    print(f"    Caps:   {len(skill.capabilities)}")
                    for cap in skill.capabilities:
                        print(f"      - {cap.type}: {cap.scope}")
                    print("")
    else:
        print(f"  Skills directory not found: {skills_dir}")
        print(f"  Run from the starter-stack root directory.")
