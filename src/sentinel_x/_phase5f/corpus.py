"""Private Phase-5F corpus contracts, audit, and freeze/export support."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, cast

from sentinel_x.dependency.models import DependencyRelation

from ._common import validate_bounded_text, validate_case_id, validate_evidence_ref
from .gold import CaseGold
from .visible import CaseSource, project_case_evidence, visible_evidence_refs

PHASE5F_CORPUS_SCHEMA_VERSION: Final[str] = "sentinel-x.phase5f-corpus.v1"
PHASE5F_CORPUS_PROVENANCE_SCHEMA_VERSION: Final[str] = (
    "sentinel-x.phase5f-corpus-provenance.v1"
)
PHASE5F_SIDECAR_SCHEMA_VERSION: Final[str] = "sentinel-x.phase5f-readonly-sidecar.v1"
PHASE5F_TRANSFORMATION_VERSION: Final[str] = "phase5f-corpus-transform-v1"

EMPIRICAL_EFFECT_FAMILY: Final[str] = "empirical_effect"
EMPIRICAL_BOUNDED_NEGATIVE_FAMILY: Final[str] = "empirical_bounded_negative"
INSUFFICIENT_COVERAGE_FAMILY: Final[str] = "insufficient_coverage"
REVERSE_COUNTEREVIDENCE_FAMILY: Final[str] = "reverse_counterevidence"
TEMPORAL_DISTRACTOR_FAMILY: Final[str] = "temporal_distractor"
CROSS_BOOT_INVALID_FAMILY: Final[str] = "cross_boot_invalid"
MULTIPLE_CANDIDATE_AMBIGUITY_FAMILY: Final[str] = "multiple_candidate_ambiguity"

_EMPIRICAL_FAMILIES = frozenset(
    {EMPIRICAL_EFFECT_FAMILY, EMPIRICAL_BOUNDED_NEGATIVE_FAMILY}
)
_ADVERSARIAL_FAMILIES = frozenset(
    {
        INSUFFICIENT_COVERAGE_FAMILY,
        REVERSE_COUNTEREVIDENCE_FAMILY,
        TEMPORAL_DISTRACTOR_FAMILY,
        CROSS_BOOT_INVALID_FAMILY,
        MULTIPLE_CANDIDATE_AMBIGUITY_FAMILY,
    }
)
_ALL_FAMILIES = _EMPIRICAL_FAMILIES | _ADVERSARIAL_FAMILIES
_MAX_LINEAGE_TOKEN_CHARS = 512
_MAX_TRANSFORMATION_DETAIL_BYTES = 64 * 1024

REQUIRES_EMPIRICAL_CASE_IDS: Final[tuple[str, ...]] = tuple(
    f"CASE-{index:04d}" for index in range(1, 9)
)
WANTS_EMPIRICAL_CASE_IDS: Final[tuple[str, ...]] = tuple(
    f"CASE-{index:04d}" for index in range(9, 17)
)
EMPIRICAL_EXECUTION_ORDER: Final[tuple[str, ...]] = (
    "CASE-0001",
    "CASE-0009",
    "CASE-0010",
    "CASE-0002",
    "CASE-0003",
    "CASE-0011",
    "CASE-0012",
    "CASE-0004",
    "CASE-0005",
    "CASE-0013",
    "CASE-0014",
    "CASE-0006",
    "CASE-0007",
    "CASE-0015",
    "CASE-0016",
    "CASE-0008",
)
ADVERSARIAL_CASE_PLAN: Final[tuple[tuple[str, str, str], ...]] = (
    ("CASE-0017", INSUFFICIENT_COVERAGE_FAMILY, "CASE-0009"),
    ("CASE-0018", INSUFFICIENT_COVERAGE_FAMILY, "CASE-0010"),
    ("CASE-0019", INSUFFICIENT_COVERAGE_FAMILY, "CASE-0011"),
    ("CASE-0020", INSUFFICIENT_COVERAGE_FAMILY, "CASE-0012"),
    ("CASE-0021", REVERSE_COUNTEREVIDENCE_FAMILY, "CASE-0001"),
    ("CASE-0022", REVERSE_COUNTEREVIDENCE_FAMILY, "CASE-0002"),
    ("CASE-0023", REVERSE_COUNTEREVIDENCE_FAMILY, "CASE-0003"),
    ("CASE-0024", REVERSE_COUNTEREVIDENCE_FAMILY, "CASE-0004"),
    ("CASE-0025", TEMPORAL_DISTRACTOR_FAMILY, "CASE-0005"),
    ("CASE-0026", TEMPORAL_DISTRACTOR_FAMILY, "CASE-0006"),
    ("CASE-0027", TEMPORAL_DISTRACTOR_FAMILY, "CASE-0007"),
    ("CASE-0028", TEMPORAL_DISTRACTOR_FAMILY, "CASE-0008"),
    ("CASE-0029", CROSS_BOOT_INVALID_FAMILY, "CASE-0013"),
    ("CASE-0030", CROSS_BOOT_INVALID_FAMILY, "CASE-0014"),
    ("CASE-0031", CROSS_BOOT_INVALID_FAMILY, "CASE-0015"),
    ("CASE-0032", CROSS_BOOT_INVALID_FAMILY, "CASE-0016"),
    ("CASE-0033", MULTIPLE_CANDIDATE_AMBIGUITY_FAMILY, "CASE-0001"),
    ("CASE-0034", MULTIPLE_CANDIDATE_AMBIGUITY_FAMILY, "CASE-0003"),
    ("CASE-0035", MULTIPLE_CANDIDATE_AMBIGUITY_FAMILY, "CASE-0005"),
    ("CASE-0036", MULTIPLE_CANDIDATE_AMBIGUITY_FAMILY, "CASE-0007"),
)
CORPUS_V1_CASE_IDS: Final[tuple[str, ...]] = tuple(
    f"CASE-{index:04d}" for index in range(1, 37)
)
HARD_CASE_IDS: Final[frozenset[str]] = frozenset(
    {*WANTS_EMPIRICAL_CASE_IDS, *(case_id for case_id, _, _ in ADVERSARIAL_CASE_PLAN)}
)


@dataclass(frozen=True, slots=True)
class CorpusCase:
    """Bind one visible case to hidden truth, lineage, and derivation provenance."""

    source: CaseSource
    gold: CaseGold
    scenario_family: str
    lineage_by_ref: Mapping[str, str]
    parent_case_id: str | None = None
    transformation: str | None = None
    transformation_version: str | None = None
    transformation_details: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, CaseSource):
            raise ValueError("source must be a CaseSource")
        if not isinstance(self.gold, CaseGold):
            raise ValueError("gold must be a CaseGold")
        if self.source.case_id != self.gold.case_id:
            raise ValueError("visible source and hidden gold case_id must match")
        if self.scenario_family not in _ALL_FAMILIES:
            raise ValueError("scenario_family is not a Phase-5F corpus family")
        if not isinstance(self.lineage_by_ref, Mapping):
            raise ValueError("lineage_by_ref must be a mapping")

        lineage: dict[str, str] = {}
        for ref, token in self.lineage_by_ref.items():
            ref = validate_evidence_ref(ref)
            lineage[ref] = validate_bounded_text(
                token,
                field_name=f"lineage_by_ref[{ref}]",
                max_chars=_MAX_LINEAGE_TOKEN_CHARS,
            )
        source_refs = _source_refs(self.source)
        if set(lineage) != source_refs:
            raise ValueError(
                "lineage_by_ref must cover exactly all visible source refs"
            )
        gold_refs = {
            *self.gold.supporting_evidence_refs,
            *self.gold.invalid_evidence_refs,
            *self.gold.counterevidence_refs,
        }
        if not gold_refs <= source_refs:
            raise ValueError(
                "hidden gold references must exist in visible case evidence"
            )

        if self.scenario_family in _EMPIRICAL_FAMILIES:
            if self.gold.origin != "empirical":
                raise ValueError(
                    "empirical scenario family requires empirical gold origin"
                )
            if any(
                value is not None
                for value in (
                    self.parent_case_id,
                    self.transformation,
                    self.transformation_version,
                    self.transformation_details,
                )
            ):
                raise ValueError(
                    "empirical cases cannot claim adversarial transformation"
                )
        else:
            if self.gold.origin != "adversarial":
                raise ValueError(
                    "adversarial scenario family requires adversarial gold origin"
                )
            if self.parent_case_id is None:
                raise ValueError("adversarial case requires parent_case_id")
            validate_case_id(self.parent_case_id)
            if self.parent_case_id == self.source.case_id:
                raise ValueError("adversarial case cannot be its own parent")
            if self.transformation != self.scenario_family:
                raise ValueError(
                    "transformation must equal adversarial scenario family"
                )
            if self.transformation_version != PHASE5F_TRANSFORMATION_VERSION:
                raise ValueError("unexpected adversarial transformation version")
            if not isinstance(self.transformation_details, Mapping):
                raise ValueError("adversarial case requires transformation_details")
            object.__setattr__(
                self,
                "transformation_details",
                _freeze_json_mapping(
                    self.transformation_details,
                    field_name="transformation_details",
                    max_bytes=_MAX_TRANSFORMATION_DETAIL_BYTES,
                ),
            )
        object.__setattr__(
            self,
            "lineage_by_ref",
            MappingProxyType(dict(sorted(lineage.items()))),
        )

    def provenance_dict(self) -> dict[str, object]:
        return {
            "schema_version": PHASE5F_CORPUS_PROVENANCE_SCHEMA_VERSION,
            "case_id": self.source.case_id,
            "scenario_family": self.scenario_family,
            "origin": self.gold.origin,
            "source_run_group": self.gold.source_run_group,
            "parent_case_id": self.parent_case_id,
            "transformation": self.transformation,
            "transformation_version": self.transformation_version,
            "transformation_details": (
                None
                if self.transformation_details is None
                else _thaw_json(self.transformation_details)
            ),
            "lineage_by_ref": dict(self.lineage_by_ref),
        }


def empirical_relation_for_case(case_id: str) -> DependencyRelation:
    validate_case_id(case_id)
    if case_id in REQUIRES_EMPIRICAL_CASE_IDS:
        return DependencyRelation.REQUIRES
    if case_id in WANTS_EMPIRICAL_CASE_IDS:
        return DependencyRelation.WANTS
    raise ValueError("case_id is not an empirical corpus-v1 case")


def audit_corpus_v1(cases: Iterable[CorpusCase]) -> dict[str, object]:
    items = tuple(cases)
    if len(items) != 36:
        raise ValueError("corpus-v1 requires exactly 36 cases")
    by_id: dict[str, CorpusCase] = {}
    for item in items:
        if not isinstance(item, CorpusCase):
            raise ValueError("corpus must contain CorpusCase values")
        if item.source.case_id in by_id:
            raise ValueError("corpus case IDs must be unique")
        by_id[item.source.case_id] = item
    if set(by_id) != set(CORPUS_V1_CASE_IDS):
        raise ValueError("corpus-v1 case ID set is incomplete or unexpected")

    expected_counts = {
        EMPIRICAL_EFFECT_FAMILY: 8,
        EMPIRICAL_BOUNDED_NEGATIVE_FAMILY: 8,
        INSUFFICIENT_COVERAGE_FAMILY: 4,
        REVERSE_COUNTEREVIDENCE_FAMILY: 4,
        TEMPORAL_DISTRACTOR_FAMILY: 4,
        CROSS_BOOT_INVALID_FAMILY: 4,
        MULTIPLE_CANDIDATE_AMBIGUITY_FAMILY: 4,
    }
    if dict(Counter(item.scenario_family for item in items)) != expected_counts:
        raise ValueError(
            "corpus-v1 scenario-family counts do not match preregistration"
        )
    empirical = [item for item in items if item.gold.origin == "empirical"]
    adversarial = [item for item in items if item.gold.origin == "adversarial"]
    if len(empirical) != 16 or len(adversarial) != 20:
        raise ValueError("corpus-v1 requires 16 empirical and 20 adversarial cases")
    if len({item.gold.source_run_group for item in empirical}) != 16:
        raise ValueError("each empirical case requires a unique source_run_group")

    for item in adversarial:
        if item.parent_case_id is None:
            raise AssertionError("adversarial parent required by CorpusCase")
        parent = by_id.get(item.parent_case_id)
        if parent is None or parent.gold.origin != "empirical":
            raise ValueError("adversarial parent must exist and be empirical")
        if item.gold.source_run_group != parent.gold.source_run_group:
            raise ValueError("adversarial source_run_group must equal empirical parent")

    for item in items:
        projections = [
            project_case_evidence(item.source, condition)
            for condition in ("raw", "minimal", "full")
        ]
        for projection in projections[1:]:
            for field in ("case_id", "task", "environment", "schema_version"):
                if projection[field] != projections[0][field]:
                    raise ValueError("RAW/MINIMAL/FULL outer lineage drift")
        visible_union = frozenset().union(
            *(
                visible_evidence_refs(item.source, condition)
                for condition in ("raw", "minimal", "full")
            )
        )
        if visible_union != set(item.lineage_by_ref):
            raise ValueError("case lineage does not cover all projected evidence refs")

    return {
        "schema_version": PHASE5F_CORPUS_SCHEMA_VERSION,
        "case_count": 36,
        "empirical_case_count": 16,
        "adversarial_case_count": 20,
        "hard_case_count": 28,
        "family_counts": dict(sorted(expected_counts.items())),
        "hard_case_ids": sorted(HARD_CASE_IDS),
        "scored_reasoner_outputs_present": False,
    }


def write_corpus_v1(
    cases: Iterable[CorpusCase],
    output_dir: str | os.PathLike[str],
    *,
    observer_pilot: Mapping[str, object],
) -> dict[str, object]:
    items = tuple(sorted(cases, key=lambda item: item.source.case_id))
    summary = audit_corpus_v1(items)
    pilot = _json_copy(observer_pilot, field_name="observer_pilot")
    if not isinstance(pilot, dict):
        raise AssertionError("observer pilot copy must be a dict")
    if pilot.get("material_observer_effect_detected") is not False:
        raise ValueError("observer pilot must explicitly pass before corpus freeze")

    target = Path(output_dir)
    if target.exists():
        raise ValueError("output_dir must not already exist")
    temp = target.with_name(f".{target.name}.tmp")
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(mode=0o700, parents=True)
    try:
        visible_dir = temp / "visible"
        hidden_dir = temp / "hidden"
        visible_dir.mkdir(mode=0o755)
        hidden_dir.mkdir(mode=0o700)
        for condition in ("raw", "minimal", "full"):
            _write_jsonl(
                visible_dir / f"{condition}.jsonl",
                (project_case_evidence(item.source, condition) for item in items),
                mode=0o644,
            )
        _write_jsonl(
            hidden_dir / "gold.jsonl",
            (item.gold.to_dict() for item in items),
            mode=0o600,
        )
        _write_jsonl(
            hidden_dir / "provenance.jsonl",
            (item.provenance_dict() for item in items),
            mode=0o600,
        )
        _write_json(temp / "observer_pilot.json", pilot, mode=0o600)
        manifest = {
            **summary,
            "empirical_execution_order": list(EMPIRICAL_EXECUTION_ORDER),
            "adversarial_case_plan": [
                {
                    "case_id": case_id,
                    "scenario_family": family,
                    "parent_case_id": parent,
                }
                for case_id, family, parent in ADVERSARIAL_CASE_PLAN
            ],
            "transformation_version": PHASE5F_TRANSFORMATION_VERSION,
            "condition_names_not_serialized_inside_evidence_bundles": True,
            "hidden_gold_separate_from_visible_views": True,
            "adversarial_variants_are_not_independent_empirical_replications": True,
        }
        _write_json(temp / "manifest.json", manifest, mode=0o644)
        _write_sha256sums(temp)
        os.replace(temp, target)
    except BaseException:
        if temp.exists():
            shutil.rmtree(temp)
        raise
    return summary


def _source_refs(source: CaseSource) -> set[str]:
    refs: set[str] = set()
    for item in source.evidence_items:
        ref = item["ref"]
        if not isinstance(ref, str):
            raise AssertionError("CaseSource ref must be text")
        refs.add(ref)
    return refs


def _required_text(mapping: Mapping[str, object], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be non-empty text")
    return value


def _required_nonnegative_int(mapping: Mapping[str, object], field: str) -> int:
    value = mapping.get(field)
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _required_positive_int(mapping: Mapping[str, object], field: str) -> int:
    value = _required_nonnegative_int(mapping, field)
    if value == 0:
        raise ValueError(f"{field} must be positive")
    return value


def _json_copy(value: object, *, field_name: str) -> object:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be finite JSON-compatible data") from exc


def _freeze_json_mapping(
    value: Mapping[str, object],
    *,
    field_name: str,
    max_bytes: int,
) -> Mapping[str, object]:
    copied = _json_copy(value, field_name=field_name)
    if not isinstance(copied, dict):
        raise AssertionError("JSON mapping copy must remain a dict")
    encoded = json.dumps(copied, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > max_bytes:
        raise ValueError(f"{field_name} exceeds {max_bytes} bytes")
    return MappingProxyType(cast(dict[str, object], copied))


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw_json(child) for child in value]
    return value


def _write_json(path: Path, payload: object, *, mode: int) -> None:
    path.write_text(
        json.dumps(
            payload, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(mode)


def _write_jsonl(path: Path, payloads: Iterable[object], *, mode: int) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(
                json.dumps(
                    payload,
                    sort_keys=True,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
            )
            handle.write("\n")
    path.chmod(mode)


def _write_sha256sums(root: Path) -> None:
    paths = sorted(
        path for path in root.rglob("*") if path.is_file() and path.name != "SHA256SUMS"
    )
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root).as_posix()}"
        for path in paths
    ]
    sums = root / "SHA256SUMS"
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sums.chmod(0o644)
