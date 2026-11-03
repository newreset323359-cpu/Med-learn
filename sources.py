from dataclasses import dataclass

@dataclass(frozen=True)
class Source:
    key: str
    name: str
    url: str
    counselling_system: str

SOURCES = [
    Source(
        key="mcc_pg_archive",
        name="Medical Counselling Committee — PG Archive",
        url="https://mcc.nic.in/archive-pg/",
        counselling_system="MCC All India",
    ),
    Source(
        key="maha_cap_2026_27",
        name="Maharashtra CET Cell — CAP 2026–27",
        url="https://cetcell.mahacet.org/cap-_2026-27/",
        counselling_system="Maharashtra State",
    ),
    Source(
        key="maha_pg_2025_portal",
        name="Maharashtra NEET-PGM 2025 portal",
        url="https://medicalug2025.mahacet.org/NEET-PGM-2025/login",
        counselling_system="Maharashtra State",
    ),
]
