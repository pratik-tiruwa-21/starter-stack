"""
AgentProxy — Layer 4 Reference Monitor (Skeleton)
ClawdContext OS

Sits between LLM and every tool call.
Enforces capability grants, rate limits, and information flow control.

Status: SKELETON — not functional yet. This defines the interface.
"""

import json
import hashlib
import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Decision(Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    HUMAN_GATE = "HUMAN_GATE"


class Severity(Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass
class ToolCallRequest:
    """Incoming tool call from the LLM."""
    skill: str
    action: str
    target: str
    session_id: str
    context: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())


@dataclass
class PolicyResult:
    """Result of a single policy evaluation."""
    policy: str
    passed: bool
    reason: str = ""
    severity: Severity = Severity.INFO


@dataclass
class ProxyDecision:
    """Final decision from the proxy."""
    decision: Decision
    request: ToolCallRequest
    policy_results: list[PolicyResult] = field(default_factory=list)
    audit_id: str = ""
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.value,
            "skill": self.request.skill,
            "action": self.request.action,
            "target": self.request.target,
            "policy_results": [
                {"policy": r.policy, "passed": r.passed, "reason": r.reason}
                for r in self.policy_results
            ],
            "audit_id": self.audit_id,
            "latency_ms": self.latency_ms,
        }


class CapabilityChecker:
    """
    Evaluates per-skill capability grants.
    Consumes: security/policies/capabilities.rego
    """

    def __init__(self, policy_path: str = "security/policies/capabilities.rego"):
        self.policy_path = policy_path
        # TODO: Load OPA policy via opa-python or subprocess

    def check(self, request: ToolCallRequest) -> PolicyResult:
        """Check if the skill has the capability for this action."""
        # TODO: Evaluate against OPA
        # For now, default deny
        return PolicyResult(
            policy="capabilities",
            passed=False,
            reason="CapabilityChecker not yet implemented — default DENY",
            severity=Severity.HIGH,
        )


class RateLimiter:
    """
    Enforces token budgets and request rate limits.
    Consumes: security/policies/rate-limits.rego
    """

    def __init__(self):
        self._counters: dict[str, list[float]] = {}

    def check(self, request: ToolCallRequest) -> PolicyResult:
        """Check rate limits for this skill."""
        # TODO: Implement sliding window rate limiting
        return PolicyResult(
            policy="rate-limits",
            passed=True,
            reason="Rate limiter skeleton — allowing all",
            severity=Severity.INFO,
        )


class FlowController:
    """
    Bell-LaPadula information flow control.
    No Read Up, No Write Down.
    Consumes: security/policies/flow-control.rego
    """

    LEVELS = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "RESTRICTED": 3}

    def check(self, request: ToolCallRequest) -> PolicyResult:
        """Enforce information flow rules."""
        # TODO: Classify skill clearance and target security level
        return PolicyResult(
            policy="flow-control",
            passed=True,
            reason="FlowController skeleton — allowing all",
            severity=Severity.INFO,
        )


class SemanticFirewall:
    """
    Optional second LLM that validates intent.
    Catches sophisticated attacks that bypass pattern matching.
    """

    def check(self, request: ToolCallRequest) -> PolicyResult:
        """Validate intent via LLM."""
        # TODO: Call second LLM with intent validation prompt
        return PolicyResult(
            policy="semantic-firewall",
            passed=True,
            reason="SemanticFirewall not configured — bypassing",
            severity=Severity.INFO,
        )


class AuditLogger:
    """
    Append-only, cryptographically-chained audit log.
    Every decision is recorded with a hash chain.
    """

    def __init__(self, log_path: str = "audit.log"):
        self.log_path = log_path
        self._prev_hash = "GENESIS"

    def log(self, decision: ProxyDecision) -> str:
        """Log a decision and return the audit ID."""
        entry = {
            "timestamp": decision.request.timestamp,
            "skill": decision.request.skill,
            "action": decision.request.action,
            "target": decision.request.target,
            "decision": decision.decision.value,
            "policies": [r.to_dict() if hasattr(r, 'to_dict') else {"policy": r.policy, "passed": r.passed} 
                        for r in decision.policy_results],
            "prev_hash": self._prev_hash,
        }

        entry_json = json.dumps(entry, sort_keys=True)
        entry_hash = hashlib.sha256(entry_json.encode()).hexdigest()[:16]
        self._prev_hash = entry_hash

        # TODO: Write to append-only file
        return f"audit_{entry_hash}"


class AgentProxy:
    """
    The reference monitor. Intercepts every tool call.

    Usage:
        proxy = AgentProxy()
        decision = proxy.evaluate(request)
        if decision.decision == Decision.ALLOW:
            # Execute the tool call
        elif decision.decision == Decision.HUMAN_GATE:
            # Ask for human approval
        else:
            # Deny and log
    """

    HUMAN_GATE_ACTIONS = {"file_delete", "exec", "credential_access"}

    def __init__(self):
        self.capability_checker = CapabilityChecker()
        self.rate_limiter = RateLimiter()
        self.flow_controller = FlowController()
        self.semantic_firewall = SemanticFirewall()
        self.audit_logger = AuditLogger()

    def evaluate(self, request: ToolCallRequest) -> ProxyDecision:
        """
        Evaluate a tool call request against all policies.

        Order of evaluation:
        1. Capability check (hard gate)
        2. Rate limit check (hard gate)
        3. Flow control check (hard gate)
        4. Semantic firewall (soft gate — configurable)
        5. Human gate (for destructive actions)
        """
        import time
        start = time.monotonic()

        results: list[PolicyResult] = []

        # 1. Capability check
        cap_result = self.capability_checker.check(request)
        results.append(cap_result)
        if not cap_result.passed:
            decision = ProxyDecision(
                decision=Decision.DENY,
                request=request,
                policy_results=results,
                latency_ms=(time.monotonic() - start) * 1000,
            )
            decision.audit_id = self.audit_logger.log(decision)
            return decision

        # 2. Rate limit check
        rate_result = self.rate_limiter.check(request)
        results.append(rate_result)
        if not rate_result.passed:
            decision = ProxyDecision(
                decision=Decision.DENY,
                request=request,
                policy_results=results,
                latency_ms=(time.monotonic() - start) * 1000,
            )
            decision.audit_id = self.audit_logger.log(decision)
            return decision

        # 3. Flow control
        flow_result = self.flow_controller.check(request)
        results.append(flow_result)
        if not flow_result.passed:
            decision = ProxyDecision(
                decision=Decision.DENY,
                request=request,
                policy_results=results,
                latency_ms=(time.monotonic() - start) * 1000,
            )
            decision.audit_id = self.audit_logger.log(decision)
            return decision

        # 4. Semantic firewall
        sem_result = self.semantic_firewall.check(request)
        results.append(sem_result)

        # 5. Human gate
        if request.action in self.HUMAN_GATE_ACTIONS:
            decision = ProxyDecision(
                decision=Decision.HUMAN_GATE,
                request=request,
                policy_results=results,
                latency_ms=(time.monotonic() - start) * 1000,
            )
            decision.audit_id = self.audit_logger.log(decision)
            return decision

        # All checks passed
        decision = ProxyDecision(
            decision=Decision.ALLOW,
            request=request,
            policy_results=results,
            latency_ms=(time.monotonic() - start) * 1000,
        )
        decision.audit_id = self.audit_logger.log(decision)
        return decision


# ── Entry Point ─────────────────────────────────────────────────
if __name__ == "__main__":
    print("AgentProxy — Layer 4 Reference Monitor")
    print("Status: SKELETON — not functional yet")
    print("")
    print("This module defines the interface for:")
    print("  - CapabilityChecker (per-skill grants)")
    print("  - RateLimiter (token budgets)")
    print("  - FlowController (Bell-LaPadula)")
    print("  - SemanticFirewall (intent validation)")
    print("  - AuditLogger (hash-chained audit trail)")
    print("")
    print("See agent-proxy/README.md for architecture.")
    print("Contribute: https://github.com/ClawdContextOS/starter-stack")
