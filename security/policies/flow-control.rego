# ClawdContext OS — Information Flow Control Policy
# Layer 4: AgentProxy Reference Monitor
# Implements Bell-LaPadula model for agent information flow
#
# Usage: opa eval -d flow-control.rego -i input.json "data.flowcontrol.allow"
#
# Bell-LaPadula rules:
#   - No Read Up: a subject at level L cannot read objects at level > L
#   - No Write Down: a subject at level L cannot write objects at level < L
#   - This prevents untrusted skills from reading credentials
#     and prevents trusted data from flowing to untrusted channels

package flowcontrol

import rego.v1

# Security levels (ordered LOW → HIGH)
# Higher number = higher classification
security_levels := {
    "PUBLIC": 0,       # Public data, anyone can read
    "INTERNAL": 1,     # Internal workspace data
    "CONFIDENTIAL": 2, # API keys, tokens, credentials
    "RESTRICTED": 3,   # SSH keys, signing keys, admin secrets
}

# Default deny
default allow := false

# Allow if Bell-LaPadula rules are satisfied
allow if {
    not violates_no_read_up
    not violates_no_write_down
    not crosses_trust_boundary
}

# No Read Up: subject clearance must be >= object classification
violates_no_read_up if {
    input.action.type == "file_read"
    subject_level := security_levels[input.subject.clearance]
    object_level := security_levels[input.object.classification]
    subject_level < object_level
}

# No Write Down: subject clearance must be <= object classification
# (prevents leaking classified data to public channels)
violates_no_write_down if {
    input.action.type == "file_write"
    subject_level := security_levels[input.subject.clearance]
    object_level := security_levels[input.object.classification]
    subject_level > object_level
}

# Trust boundary: untrusted skills cannot access any CONFIDENTIAL+ data
crosses_trust_boundary if {
    input.subject.trust == "UNTRUSTED"
    object_level := security_levels[input.object.classification]
    object_level >= 2  # CONFIDENTIAL or higher
}

# Network flow control: data classification determines allowed destinations
deny["cannot send CONFIDENTIAL data over network"] if {
    input.action.type == "net"
    input.object.classification == "CONFIDENTIAL"
}

deny["cannot send RESTRICTED data over network"] if {
    input.action.type == "net"
    input.object.classification == "RESTRICTED"
}

# Classify common paths
path_classification(path) := "RESTRICTED" if {
    contains(path, ".ssh/")
}

path_classification(path) := "RESTRICTED" if {
    contains(path, "signing/private")
}

path_classification(path) := "CONFIDENTIAL" if {
    contains(path, ".env")
}

path_classification(path) := "CONFIDENTIAL" if {
    contains(path, ".aws/credentials")
}

path_classification(path) := "CONFIDENTIAL" if {
    regex.match(`_KEY|_SECRET|_TOKEN`, path)
}

path_classification(path) := "INTERNAL" if {
    startswith(path, "/workspace/")
    not contains(path, ".env")
    not contains(path, ".ssh/")
    not contains(path, ".aws/")
}

path_classification(_) := "PUBLIC"

# Skill trust classification
skill_clearance(skill) := "PUBLIC" if {
    skill.signature == ""
}

skill_clearance(skill) := "PUBLIC" if {
    startswith(skill.signature, "UNSIGNED")
}

skill_clearance(skill) := "PUBLIC" if {
    startswith(skill.signature, "FORGED")
}

skill_clearance(_) := "INTERNAL"

# Deny reasons
deny["no-read-up violation: insufficient clearance to read this data"] if {
    violates_no_read_up
}

deny["no-write-down violation: cannot write classified data to lower level"] if {
    violates_no_write_down
}

deny["trust boundary violation: untrusted skill accessing classified data"] if {
    crosses_trust_boundary
}

# Aggregate
violations := deny
