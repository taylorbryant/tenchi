"""Repository-owned requirements for the historical verification receipt."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from ._openapi_operations import OperationError, project_path, read_git_snapshot

VERIFICATION_POLICY_PATH = "tenchi.toml"
VERIFICATION_POLICY_SCHEMA_VERSION = 1

type VerificationEvidenceStage = Literal[
    "check",
    "architecture",
    "openapi",
    "jobs",
    "tools",
    "evaluations",
]
type VerificationRequirement = Literal[
    "required",
    "disabled",
    "not_configured",
]
type VerificationPolicySource = Literal["repository", "default"]
type VerificationPolicyChangeSeverity = Literal[
    "breaking",
    "strengthening",
    "metadata",
]

VERIFICATION_EVIDENCE_STAGES: tuple[VerificationEvidenceStage, ...] = (
    "check",
    "architecture",
    "openapi",
    "jobs",
    "tools",
    "evaluations",
)


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    """One parsed policy, or Tenchi's strict built-in policy."""

    source: VerificationPolicySource
    requirements: tuple[tuple[VerificationEvidenceStage, VerificationRequirement], ...]

    def requirement(
        self,
        stage: VerificationEvidenceStage,
    ) -> VerificationRequirement:
        for name, requirement in self.requirements:
            if name == stage:
                return requirement
        raise AssertionError(f"unknown verification stage {stage!r}")


@dataclass(frozen=True, slots=True)
class VerificationPolicyChange:
    """A directional change from the historical policy to the current one."""

    severity: VerificationPolicyChangeSeverity
    stage: VerificationEvidenceStage | None
    message: str


@dataclass(frozen=True, slots=True)
class VerificationPolicyComparison:
    """Current and historical policy declarations tied to one Git baseline."""

    path: str
    baseline: str
    current: VerificationPolicy
    historical: VerificationPolicy
    changes: tuple[VerificationPolicyChange, ...]

    @property
    def compatible(self) -> bool:
        return not any(change.severity == "breaking" for change in self.changes)

    def enforced(self, stage: VerificationEvidenceStage) -> bool:
        """Keep a historical requirement active while a weakening is reviewed."""
        return (
            self.current.requirement(stage) == "required"
            or self.historical.requirement(stage) == "required"
        )


def verification_policy_comparison(
    root: Path,
    *,
    ref: str,
) -> VerificationPolicyComparison:
    """Load the current policy and compare it with *ref* directionally."""
    resolved_root = root.resolve()
    path = project_path(resolved_root, VERIFICATION_POLICY_PATH)
    current = _read_current_policy(path)
    snapshot = read_git_snapshot(
        resolved_root,
        ref=ref,
        snapshot=path,
        missing_text="",
    )
    historical = (
        _parse_policy(snapshot.text, label=snapshot.label)
        if snapshot.present
        else _default_policy()
    )
    return VerificationPolicyComparison(
        path=VERIFICATION_POLICY_PATH,
        baseline=snapshot.label,
        current=current,
        historical=historical,
        changes=_compare_policies(historical, current),
    )


def default_verification_policy() -> VerificationPolicy:
    """Return the fail-closed policy used when policy evidence is unavailable."""
    return _default_policy()


def _read_current_policy(path: Path) -> VerificationPolicy:
    if not path.exists():
        return _default_policy()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise OperationError(
            f"could not read verification policy {VERIFICATION_POLICY_PATH!r}: {exc}"
        ) from exc
    return _parse_policy(text, label=VERIFICATION_POLICY_PATH)


def _parse_policy(text: str, *, label: str) -> VerificationPolicy:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise OperationError(
            f"verification policy {label!r} is not valid TOML ({exc})"
        ) from exc

    unknown_top_level = sorted(set(raw) - {"schema_version", "verify"})
    if unknown_top_level:
        raise OperationError(
            f"verification policy {label!r} has unknown top-level keys: "
            f"{', '.join(unknown_top_level)}"
        )
    version = raw.get("schema_version")
    if type(version) is not int or version != VERIFICATION_POLICY_SCHEMA_VERSION:
        raise OperationError(
            f"verification policy {label!r} must declare "
            f"schema_version = {VERIFICATION_POLICY_SCHEMA_VERSION}"
        )
    verify = raw.get("verify")
    if not isinstance(verify, Mapping):
        raise OperationError(
            f"verification policy {label!r} must contain a [verify] table"
        )
    raw_verify = cast(Mapping[object, object], verify)
    if not all(isinstance(key, str) for key in raw_verify):
        raise OperationError(
            f"verification policy {label!r} contains a non-string verify key"
        )
    configured = cast(Mapping[str, object], raw_verify)
    unknown_stages = sorted(set(configured) - set(VERIFICATION_EVIDENCE_STAGES))
    if unknown_stages:
        raise OperationError(
            f"verification policy {label!r} has unknown verify stages: "
            f"{', '.join(unknown_stages)}"
        )

    requirements: list[tuple[VerificationEvidenceStage, VerificationRequirement]] = []
    for stage in VERIFICATION_EVIDENCE_STAGES:
        value = configured.get(stage)
        if value is None and stage not in configured:
            requirement: VerificationRequirement = "not_configured"
        elif type(value) is bool:
            requirement = "required" if value else "disabled"
        else:
            raise OperationError(
                f"verification policy {label!r} stage {stage!r} must be a boolean"
            )
        requirements.append((stage, requirement))
    return VerificationPolicy(
        source="repository",
        requirements=tuple(requirements),
    )


def _default_policy() -> VerificationPolicy:
    return VerificationPolicy(
        source="default",
        requirements=tuple(
            (stage, "required") for stage in VERIFICATION_EVIDENCE_STAGES
        ),
    )


def _compare_policies(
    historical: VerificationPolicy,
    current: VerificationPolicy,
) -> tuple[VerificationPolicyChange, ...]:
    changes: list[VerificationPolicyChange] = []
    if historical.source == "default" and current.source == "repository":
        changes.append(
            VerificationPolicyChange(
                severity="metadata",
                stage=None,
                message="repository verification policy added",
            )
        )
    elif historical.source == "repository" and current.source == "default":
        changes.append(
            VerificationPolicyChange(
                severity="breaking",
                stage=None,
                message="repository verification policy removed",
            )
        )

    for stage in VERIFICATION_EVIDENCE_STAGES:
        before = historical.requirement(stage)
        after = current.requirement(stage)
        if before == after:
            continue
        if before == "required" and after != "required":
            severity: VerificationPolicyChangeSeverity = "breaking"
            message = f"required {stage} evidence became {after}"
        elif before != "required" and after == "required":
            severity = "strengthening"
            message = f"{stage} evidence became required"
        else:
            severity = "metadata"
            message = f"{stage} evidence changed from {before} to {after}"
        changes.append(
            VerificationPolicyChange(
                severity=severity,
                stage=stage,
                message=message,
            )
        )
    return tuple(changes)


__all__ = [
    "VERIFICATION_EVIDENCE_STAGES",
    "VERIFICATION_POLICY_PATH",
    "VERIFICATION_POLICY_SCHEMA_VERSION",
    "VerificationEvidenceStage",
    "VerificationPolicy",
    "VerificationPolicyChange",
    "VerificationPolicyChangeSeverity",
    "VerificationPolicyComparison",
    "VerificationPolicySource",
    "VerificationRequirement",
    "default_verification_policy",
    "verification_policy_comparison",
]
