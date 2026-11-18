"""Synthetic aggregate observations and an offline reader double; tests only.

These invented aggregates are never a live-source fallback and are deliberately
kept out of the shipped ``growthos`` package. ``FixtureReaderStore`` reproduces
the read surface of ``growthos.postgres.PostgresStore`` so the CLI can be
exercised end to end without a database.
"""

from contextlib import contextmanager

from growthos.contracts import VOLUMES, MonthlyData, validate_data


def demo_payload() -> dict:
    observations = (
        ("2026-08", "harbor", "Harbor", "T1", (100, 40, 20, 10, 8, 4)),
        ("2026-08", "vale", "Vale", "T3", (0, 0, 0, 0, 0, 0)),
        ("2026-09", "harbor", "Harbor", "T1", (100, 50, 30, 15, 12, 6)),
        ("2026-09", "vale", "Vale", "T3", (300, 30, 10, 5, 4, 3)),
        ("2026-10", "harbor", "Harbor", "T1", (200, 80, 40, 16, 20, 8)),
        ("2026-10", "vale", "Vale", "T3", (100, 20, 20, 12, 8, 5)),
    )
    months = ["2026-08", "2026-09", "2026-10"]
    return {
        "nonproduction": True, "months": months,
        "month_metadata": [
            {
                "month": m, "extracted_at": "2026-11-02T00:01:00+00:00", "business_timezone": "Asia/Kolkata",
                "source_reads": [
                    {"source": source, "month": m,
                     "started_at": f"2026-11-02T00:00:{i * 10:02d}+00:00",
                     "completed_at": f"2026-11-02T00:00:{i * 10 + 5:02d}+00:00"}
                    for i, source in enumerate(("installs", "registrations", "ftu"))
                ],
            } for m in months
        ],
        "facts": [
            {"month": m, "city_key": key, "city": city, "tier": tier, "source_cities": [city], **dict(zip(VOLUMES, counts))}
            for m, key, city, tier, counts in observations
        ],
    }


def demo_data() -> MonthlyData:
    return validate_data(demo_payload())


class FixtureReaderStore:
    """Read-only double mirroring the subset of ``PostgresStore`` the CLI uses."""

    def __init__(self, data: MonthlyData):
        self._data = data

    def available_months(self) -> list[str]:
        return list(self._data.data["months"])

    def load(self, months: list[str] | None = None) -> MonthlyData:
        data = self._data.data
        selected = data["months"][-2:] if months is None else sorted(set(months))
        return validate_data({
            **data,
            "months": selected,
            "facts": [r for r in data["facts"] if r["month"] in selected],
            "month_metadata": [r for r in data["month_metadata"] if r["month"] in selected],
        })

    def catalog(self, city_offset: int = 0) -> dict:
        from growthos.evidence import CITY_PAGE_SIZE, status_result, validate_city_offset
        validate_city_offset(city_offset)
        data = self._data.data
        cities = {r["city_key"]: {k: r[k] for k in ("city_key", "city", "tier")} for r in data["facts"]}
        return status_result(
            months=data["months"], month_metadata=data["month_metadata"],
            fact_count=len(data["facts"]), cities=[cities[k] for k in sorted(cities)][city_offset:city_offset + CITY_PAGE_SIZE],
            city_count=len(cities), tiers=sorted({r["tier"] for r in data["facts"]}),
            city_offset=city_offset, nonproduction=data["nonproduction"],
        )

    def load_targets(self, value: str):
        return demo_targets(value)


def demo_targets(value="2026-10"):
    from growthos.targets import validate_targets
    return validate_targets({
        "month": value, "source_checksum": "a" * 64, "imported_at": "2026-11-02T00:01:00+00:00",
        "rows": [
            {"month": value, "kind": kind, "city_key": key, "city": city,
             "target": count, "source_targets": [{"city": city, "target": count}]}
            for kind, key, city, count in (
                ("city", "harbor", "Harbor", 80), ("city", "vale", "Vale", 50),
                ("new_launch", "", "New launch", 60), ("overall", "", "Grand Total", 150),
            )
        ],
    })


@contextmanager
def fixture_reader():
    """Stand-in for ``growthos.cli.connect_reader`` in offline tests."""
    yield FixtureReaderStore(demo_data())
