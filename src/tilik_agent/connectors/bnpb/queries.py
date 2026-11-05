"""Typed reads over synchronized BNPB aggregate facts."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tilik_agent.connectors.bnpb.client import BASE, RESOURCES
from tilik_agent.connectors.bnpb.models import BnpbFact, BnpbSnapshot


def query_series(
    session: Session,
    *,
    measure: str = "events",
    geography_level: str = "national",
    geography_name: str | None = None,
    disaster_type: str | None = None,
    start_year: int = 2010,
    end_year: int = 2024,
) -> dict[str, Any]:
    """Reject combinations absent from the four reviewed BNPB resources."""
    if measure not in {"events", "affected"} or geography_level not in {"national", "province", "district"}:
        raise ValueError("Unsupported BNPB measure or geography level")
    if not 2010 <= start_year <= end_year <= 2024:
        raise ValueError("BNPB coverage is 2010–2024")
    if geography_level == "national":
        if not disaster_type:
            raise ValueError("National BNPB series requires one disaster type")
        if geography_name:
            raise ValueError("National series cannot filter a province or district")
        kind = "type_affected" if measure == "affected" else "type_events"
    else:
        if measure != "events" or disaster_type:
            raise ValueError("The sampled BNPB province/district series covers all disaster types only")
        kind = "province_events" if geography_level == "province" else "district_events"
    resource_id = RESOURCES[kind]
    snapshot = session.scalar(
        select(BnpbSnapshot)
        .where(BnpbSnapshot.resource_id == resource_id)
        .order_by(BnpbSnapshot.id.desc())
        .limit(1)
    )
    if snapshot is None:
        raise ValueError("BNPB data is not synchronized yet; run `tilik ingest bnpb`")
    statement = select(BnpbFact).where(
        BnpbFact.snapshot_id == snapshot.id,
        BnpbFact.year >= start_year,
        BnpbFact.year <= end_year,
    )
    if geography_name:
        statement = statement.where(func.lower(BnpbFact.geography_name) == geography_name.casefold())
    if disaster_type:
        statement = statement.where(func.lower(BnpbFact.disaster_type) == disaster_type.casefold())
    facts = session.scalars(statement.order_by(BnpbFact.year, BnpbFact.geography_code).limit(601)).all()
    if not facts:
        raise ValueError("No BNPB rows match that exact place or disaster type")
    if len(facts) > 600:
        raise ValueError("BNPB query exceeds 600 facts; narrow the place or year range")
    if geography_level == "province" and start_year < end_year and any(
        (fact.geography_code or "").startswith("9") for fact in facts
    ):
        raise ValueError("Papua province boundaries are not validated for cross-year comparison")
    calculations = []
    if len(facts) <= 60:
        groups = defaultdict(list)
        for fact in facts:
            groups[(fact.geography_code, fact.disaster_type)].append(fact)
        for group in groups.values():
            ordered = sorted(group, key=lambda fact: fact.year)
            for previous, current in zip(ordered, ordered[1:], strict=False):
                if current.year != previous.year + 1 or previous.value is None or current.value is None:
                    continue
                difference = current.value - previous.value
                calculations.append({
                    "from_year": previous.year, "to_year": current.year,
                    "geography_name": current.geography_name, "disaster_type": current.disaster_type,
                    "difference": difference,
                    "percent_change": round(difference / previous.value * 100, 2) if previous.value else None,
                })
    source_url = f"{BASE}/datastore_search?resource_id={resource_id}"
    return {
        "provider": "BNPB",
        "title": snapshot.title,
        "resource_id": resource_id,
        "source_url": source_url,
        "retrieved_at": snapshot.retrieved_at.isoformat(),
        "license": snapshot.license_id,
        "source_modified_at": snapshot.source_modified_at,
        "measure": measure,
        "unit": "people" if measure == "affected" else "events",
        "geography_level": geography_level,
        "disaster_type": disaster_type,
        "missing_values": sum(fact.value is None for fact in facts),
        "calculations": calculations,
        "rows": [
            {
                "fact_id": fact.id,
                "year": fact.year,
                "geography_code": fact.geography_code,
                "geography_name": fact.geography_name,
                "disaster_type": fact.disaster_type,
                "value": fact.value,
            }
            for fact in facts
        ],
    }
