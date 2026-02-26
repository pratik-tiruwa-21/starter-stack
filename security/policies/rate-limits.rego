# ClawdContext OS — Rate Limiting Policy
# Layer 4: AgentProxy Reference Monitor
# Enforces per-skill token budgets and request rate limits
#
# Usage: opa eval -d rate-limits.rego -i input.json "data.ratelimits.allow"

package ratelimits

import rego.v1

# Default deny
default allow := false

# Allow if within rate limit AND token budget
allow if {
    within_rate_limit
    within_token_budget
    not excessive_rate_limit
    not excessive_token_budget
}

# Check rate limit: requests in current window < declared limit
within_rate_limit if {
    input.skill.rate_limit_value > 0
    input.current_requests < input.skill.rate_limit_value
}

# Check token budget: tokens consumed < declared budget
within_token_budget if {
    input.skill.token_budget > 0
    input.tokens_consumed < input.skill.token_budget
}

# Flag unreasonable rate limits (likely malicious)
excessive_rate_limit if {
    input.skill.rate_limit_value > 1000
}

# Flag unreasonable token budgets (likely malicious)
excessive_token_budget if {
    input.skill.token_budget > 100000
}

# Deny reasons
deny["rate limit exceeded"] if {
    not within_rate_limit
}

deny["token budget exhausted"] if {
    not within_token_budget
}

deny["excessive rate limit declared — possible malicious skill"] if {
    excessive_rate_limit
}

deny["excessive token budget declared — possible malicious skill"] if {
    excessive_token_budget
}

# CER enforcement — deny if context efficiency is too low
deny["CER below critical threshold (0.3)"] if {
    input.cer < 0.3
}

deny["CER below target threshold (0.6) — skill loading restricted"] if {
    input.cer < 0.6
    input.action == "load_skill"
}

# Per-minute costs (estimated)
# Used for cost attribution dashboard
cost_per_request := input.skill.token_budget / input.skill.rate_limit_value

estimated_hourly_cost if {
    cost_per_request * 60
}

# Aggregate
violations := deny
