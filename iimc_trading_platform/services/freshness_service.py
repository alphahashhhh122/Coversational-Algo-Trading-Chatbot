from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from ..db import connect


FreshnessPurpose = Literal[
    "historical_research",
    "current_market",
    "broker_state",
    "reference",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class FreshnessPolicy:
    purpose: FreshnessPurpose
    max_age_seconds: int | None
    future_tolerance_seconds: int = 300
    accepted_quality: tuple[str, ...] = (
        "clean",
        "clean_with_warnings",
    )


DEFAULT_POLICIES: dict[str, FreshnessPolicy] = {
    "historical_research": FreshnessPolicy(
        purpose="historical_research",
        max_age_seconds=None,
    ),
    "current_market": FreshnessPolicy(
        purpose="current_market",
        max_age_seconds=900,
    ),
    "broker_state": FreshnessPolicy(
        purpose="broker_state",
        max_age_seconds=30,
    ),
    "reference": FreshnessPolicy(
        purpose="reference",
        max_age_seconds=86_400,
    ),
}


class FreshnessService:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def assess(
        self,
        dataset_id: str,
        purpose: FreshnessPurpose,
        *,
        reference_time: datetime | None = None,
    ) -> dict:
        policy = self._ensure_policy(dataset_id, purpose)
        now = reference_time or utc_now()
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT end_ts, quality_status, row_count
                FROM data_catalog
                WHERE dataset_id = ?
                """,
                [dataset_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            raise ValueError(f"Dataset not found: {dataset_id}")

        end_ts, quality_status, row_count = row
        status = "fresh"
        reason = "Dataset satisfies the selected purpose"
        age_seconds: int | None = None
        if quality_status not in policy.accepted_quality:
            status = "rejected"
            reason = f"Quality status {quality_status!r} is not accepted"
        elif not row_count or end_ts is None:
            status = "rejected"
            reason = "Dataset has no usable coverage"
        else:
            age_seconds = int((now - end_ts).total_seconds())
            if age_seconds < -policy.future_tolerance_seconds:
                status = "rejected"
                reason = "Dataset timestamp is unexpectedly in the future"
            elif (
                policy.max_age_seconds is not None
                and age_seconds > policy.max_age_seconds
            ):
                status = "stale"
                reason = (
                    f"Dataset age {age_seconds}s exceeds the "
                    f"{policy.max_age_seconds}s policy"
                )
            elif purpose == "historical_research":
                reason = (
                    "Historical research accepts closed datasets when "
                    "coverage and quality checks pass"
                )

        assessment_id = f"fresh_{uuid.uuid4().hex[:12]}"
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO freshness_assessments VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    assessment_id,
                    dataset_id,
                    purpose,
                    now,
                    end_ts,
                    age_seconds,
                    status,
                    reason,
                    self._policy_id(dataset_id, purpose),
                    utc_now(),
                ],
            )
        finally:
            con.close()
        return {
            "assessment_id": assessment_id,
            "dataset_id": dataset_id,
            "purpose": purpose,
            "status": status,
            "reason": reason,
            "dataset_end_ts": end_ts,
            "reference_time": now,
            "age_seconds": age_seconds,
            "max_age_seconds": policy.max_age_seconds,
            "quality_status": quality_status,
        }

    def _ensure_policy(
        self,
        dataset_id: str,
        purpose: FreshnessPurpose,
    ) -> FreshnessPolicy:
        if purpose not in DEFAULT_POLICIES:
            raise ValueError(f"Unsupported freshness purpose: {purpose}")
        default = DEFAULT_POLICIES[purpose]
        policy_id = self._policy_id(dataset_id, purpose)
        now = utc_now()
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT OR IGNORE INTO dataset_freshness_policies VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    policy_id,
                    dataset_id,
                    purpose,
                    default.max_age_seconds,
                    default.future_tolerance_seconds,
                    json.dumps(default.accepted_quality),
                    True,
                    now,
                    now,
                ],
            )
            row = con.execute(
                """
                SELECT max_age_seconds, future_tolerance_seconds,
                       accepted_quality_json
                FROM dataset_freshness_policies
                WHERE policy_id = ? AND enabled = TRUE
                """,
                [policy_id],
            ).fetchone()
        finally:
            con.close()
        if row is None:
            raise ValueError(
                f"Freshness policy is disabled for {dataset_id}/{purpose}"
            )
        return FreshnessPolicy(
            purpose=purpose,
            max_age_seconds=row[0],
            future_tolerance_seconds=row[1],
            accepted_quality=tuple(json.loads(row[2])),
        )

    @staticmethod
    def _policy_id(dataset_id: str, purpose: str) -> str:
        return f"{dataset_id}:{purpose}"
