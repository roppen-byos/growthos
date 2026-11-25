"""Synthetic aggregate observations and an offline reader double; tests only.

These invented aggregates are never a live-source fallback and are deliberately
kept out of the shipped ``growthos`` package. ``FixtureReaderStore`` reproduces
the read surface of ``growthos.postgres.PostgresStore`` so the CLI can be
exercised end to end without a database.
"""

from contextlib import contextmanager

from growthos.contracts import FUNNEL_VOLUMES, VOLUMES, MonthlyData, validate_data


def demo_payload() -> dict:
    observations = (
        ("2026-08", "harbor", "Harbor", "T1", (100, 40, 20, 10, 8, 4), (400, 200, 160, 100, 80, 60, 48, 40, 32)),
        ("2026-08", "vale", "Vale", "T3", (0, 0, 0, 0, 0, 0), (0, 0, 0, 0, 0, 0, 0, 0, 0)),
        ("2026-09", "harbor", "Harbor", "T1", (100, 50, 30, 15, 12, 6), (500, 250, 200, 125, 100, 75, 60, 50, 40)),
        ("2026-09", "vale", "Vale", "T3", (300, 30, 10, 5, 4, 3), (200, 100, 75, 50, 40, 30, 21, 20, 14)),
        ("2026-10", "harbor", "Harbor", "T1", (200, 80, 40, 16, 20, 8), (800, 480, 360, 200, 150, 160, 120, 120, 90)),
        ("2026-10", "vale", "Vale", "T3", (100, 20, 20, 12, 8, 5), (300, 120, 96, 60, 48, 40, 32, 20, 16)),
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
                    for i, source in enumerate(("installs", "registrations", "ftu", "funnel"))
                ],
            } for m in months
        ],
        "facts": [
            {"month": m, "city_key": key, "city": city, "tier": tier, "source_cities": [city],
             **dict(zip(VOLUMES, counts)), **dict(zip(FUNNEL_VOLUMES, funnel_counts))}
            for m, key, city, tier, counts, funnel_counts in observations
        ],
    }


def demo_data() -> MonthlyData:
    return validate_data(demo_payload())


class FixtureReaderStore:
    """Read-only double mirroring the subset of ``PostgresStore`` the CLI uses."""

    def __init__(self, data: MonthlyData, target_months: list[str] | None = None):
        self._data = data
        self._target_months = (
            list(data.data["months"][1:]) if target_months is None else list(target_months)
        )

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
        from growthos.classification import is_unattributed
        from growthos.evidence import CITY_PAGE_SIZE, status_result, validate_city_offset
        validate_city_offset(city_offset)
        data = self._data.data
        named = [r for r in data["facts"] if not is_unattributed(r["city_key"])]
        cities = {r["city_key"]: {k: r[k] for k in ("city_key", "city", "tier")} for r in named}
        return status_result(
            months=data["months"], month_metadata=data["month_metadata"],
            fact_count=len(data["facts"]), cities=[cities[k] for k in sorted(cities)][city_offset:city_offset + CITY_PAGE_SIZE],
            city_count=len(cities), tiers=sorted({r["tier"] for r in named}),
            city_offset=city_offset, nonproduction=data["nonproduction"],
        )

    def load_targets(self, value: str):
        return demo_targets(value)

    def target_months(self, metric: str = "platform_ftu") -> list[str]:
        return list(self._target_months)


def demo_targets(value="2026-10"):
    from growthos.targets import validate_targets
    return validate_targets({
        "month": value,
        "rows": [
            {"month": value, "kind": kind, "city_key": key, "city": city,
             "platform_ftu_target": ftu, "registrations_target": regs}
            for kind, key, city, ftu, regs in (
                ("city", "harbor", "Harbor", 80, 160), ("city", "vale", "Vale", 50, 100),
                ("new_launch", "", "New launch", 60, 120), ("overall", "", "Grand Total", 150, 300),
            )
        ],
    })


@contextmanager
def fixture_reader():
    """Stand-in for ``growthos.cli.connect_reader`` in offline tests."""
    yield FixtureReaderStore(demo_data())
