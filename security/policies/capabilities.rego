# ClawdContext OS — Capability Grants Policy
# Layer 4: AgentProxy Reference Monitor
# Enforces per-skill capability boundaries using OPA/Rego
#
# Usage: opa eval -d capabilities.rego -i input.json "data.capabilities.allow"

package capabilities

import rego.v1

# Default deny — every action must be explicitly allowed
default allow := false

# Allow if the skill has a matching capability grant
allow if {
    some grant in input.skill.capabilities
    capability_matches(grant, input.action)
}

# Deny if skill is unsigned (regardless of capabilities)
deny["unsigned skill cannot execute actions"] if {
    input.skill.signature == ""
}

deny["unsigned skill cannot execute actions"] if {
    startswith(input.skill.signature, "UNSIGNED")
}

deny["forged signature detected"] if {
    startswith(input.skill.signature, "FORGED")
}

# Deny wildcard capabilities (must be explicit)
deny["wildcard capabilities are not allowed"] if {
    some grant in input.skill.capabilities
    grant == "file_read:**"
}

deny["wildcard capabilities are not allowed"] if {
    some grant in input.skill.capabilities
    grant == "file_write:**"
}

deny["wildcard capabilities are not allowed"] if {
    some grant in input.skill.capabilities
    grant == "net:*"
}

deny["wildcard capabilities are not allowed"] if {
    some grant in input.skill.capabilities
    grant == "exec:*"
}

# Capability matching rules
capability_matches(grant, action) if {
    action.type == "file_read"
    startswith(grant, "file_read:")
    glob.match(trim_prefix(grant, "file_read:"), ["/"], action.path)
}

capability_matches(grant, action) if {
    action.type == "file_write"
    startswith(grant, "file_write:")
    glob.match(trim_prefix(grant, "file_write:"), ["/"], action.path)
}

capability_matches(grant, action) if {
    action.type == "net"
    startswith(grant, "net:")
    trim_prefix(grant, "net:") == action.host
}

capability_matches(grant, action) if {
    action.type == "exec"
    startswith(grant, "exec:")
    trim_prefix(grant, "exec:") == action.command
}

# Kernel protection — CLAUDE.md is NEVER writable
deny["CLAUDE.md is immutable — kernel protection"] if {
    input.action.type == "file_write"
    endswith(input.action.path, "CLAUDE.md")
}

# Credential protection — sensitive paths are NEVER readable
sensitive_paths := [
    "~/.ssh/",
    "~/.aws/",
    "~/.gnupg/",
    ".env",
    ".env.local",
    ".env.production",
]

deny[msg] if {
    input.action.type == "file_read"
    some path in sensitive_paths
    contains(input.action.path, path)
    msg := sprintf("credential access denied: %s", [input.action.path])
}

# Aggregate: allow only if no deny rules trigger
final_allow if {
    allow
    count(deny) == 0
}

# Report all violations
violations := deny
