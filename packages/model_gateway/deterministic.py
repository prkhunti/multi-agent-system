"""Deterministic offline model backend used for development and tests."""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

from packages.model_gateway.base import ModelRequest, ModelTask
from packages.schemas.reviews import (
    Decision,
    Evidence,
    Finding,
    FindingBatch,
    Recommendation,
    RiskCategory,
    Severity,
)


class DeterministicBackend:
    """Produce stable review outputs without network calls or credentials."""

    @property
    def name(self) -> str:
        """Return the backend identifier."""
        return "deterministic"

    async def generate(self, request: ModelRequest) -> str:
        """Generate a deterministic structured response."""
        if request.task is ModelTask.SYNTHESIZE:
            return self._synthesize(request.context).model_dump_json()

        category = {
            ModelTask.SECURITY_REVIEW: RiskCategory.SECURITY,
            ModelTask.LEGAL_REVIEW: RiskCategory.LEGAL,
            ModelTask.FINANCIAL_REVIEW: RiskCategory.FINANCIAL,
        }[request.task]
        findings = self._review(category, request.context)
        return FindingBatch(findings=findings).model_dump_json()

    def _review(self, category: RiskCategory, context: dict[str, Any]) -> list[Finding]:
        documents = context.get("documents", [])
        combined = "\n".join(str(item.get("content", "")) for item in documents).lower()
        rules = self._rules(category)
        findings: list[Finding] = []

        for rule in rules:
            matched = next((term for term in rule["terms"] if term in combined), None)
            if matched is None and not rule.get("absence", False):
                continue
            if rule.get("absence", False) and any(term in combined for term in rule["terms"]):
                continue

            evidence = self._find_evidence(documents, matched) if matched else []
            findings.append(
                Finding(
                    id=uuid5(
                        NAMESPACE_URL,
                        f"{context.get('case_id')}:{category}:{rule['title']}",
                    ),
                    category=category,
                    severity=rule["severity"],
                    title=rule["title"],
                    summary=rule["summary"],
                    evidence=evidence,
                    remediation=rule["remediation"],
                    confidence=0.96 if evidence else 0.82,
                )
            )
        return findings

    def _find_evidence(self, documents: list[dict[str, Any]], term: str | None) -> list[Evidence]:
        if term is None:
            return []
        for document in documents:
            content = str(document.get("content", ""))
            if term in content.lower():
                return [
                    Evidence(
                        document_title=str(document.get("title", "Document")),
                        quote=content[:1_000],
                        source_uri=document.get("source_uri"),
                    )
                ]
        return []

    def _synthesize(self, context: dict[str, Any]) -> Recommendation:
        findings = [Finding.model_validate(item) for item in context.get("findings", [])]
        severities = {finding.severity for finding in findings}

        if Severity.CRITICAL in severities:
            decision = Decision.ESCALATE
        elif Severity.HIGH in severities or Severity.MEDIUM in severities:
            decision = Decision.REMEDIATE
        else:
            decision = Decision.APPROVE

        actions = [finding.remediation for finding in findings]
        rationale = (
            f"The review identified {len(findings)} finding(s) across "
            f"{len({finding.category for finding in findings})} risk domain(s)."
            if findings
            else "No material risks were identified in the supplied evidence."
        )
        confidence = min((finding.confidence for finding in findings), default=0.9)
        return Recommendation(
            decision=decision,
            rationale=rationale,
            required_actions=actions,
            confidence=confidence,
        )

    def _rules(self, category: RiskCategory) -> list[dict[str, Any]]:
        if category is RiskCategory.SECURITY:
            return [
                {
                    "terms": ["shared credentials", "shared password"],
                    "severity": Severity.CRITICAL,
                    "title": "Shared credentials are permitted",
                    "summary": "The supplied security material permits shared credentials.",
                    "remediation": (
                        "Require named accounts and enforced multi-factor authentication."
                    ),
                },
                {
                    "terms": ["soc 2", "iso 27001"],
                    "absence": True,
                    "severity": Severity.MEDIUM,
                    "title": "Independent security assurance is missing",
                    "summary": "No SOC 2 or ISO 27001 assurance was found.",
                    "remediation": (
                        "Obtain a current SOC 2 Type II report or ISO 27001 certificate."
                    ),
                },
            ]
        if category is RiskCategory.LEGAL:
            return [
                {
                    "terms": ["unlimited liability"],
                    "severity": Severity.HIGH,
                    "title": "Unlimited liability clause",
                    "summary": "The contract includes an unlimited liability obligation.",
                    "remediation": "Negotiate a proportionate aggregate liability cap.",
                },
                {
                    "terms": ["automatic renewal", "auto-renewal"],
                    "severity": Severity.MEDIUM,
                    "title": "Automatic renewal requires control",
                    "summary": (
                        "The agreement renews automatically without an explicit review gate."
                    ),
                    "remediation": "Add a renewal notice and owner review requirement.",
                },
            ]
        return [
            {
                "terms": ["going concern", "negative cash flow"],
                "severity": Severity.HIGH,
                "title": "Financial continuity concern",
                "summary": "The financial evidence contains a business continuity warning.",
                "remediation": (
                    "Obtain current financial statements and a continuity mitigation plan."
                ),
            }
        ]
