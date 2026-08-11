from mobiliti_saas.worker.catalog_sync.repository import _SYNC_SUPPLIERS
from mobiliti_saas.worker.catalog_sync.service import ADAPTERS, _SUPPLIERS


EXPECTED_SUPPLIERS = (
    "cr-global", "sonara", "sunon", "alma", "lumbro", "jome", "lauco",
    "idelika", "conceptos",
)


def test_sync_and_repository_registries_append_idelika_and_conceptos():
    assert _SUPPLIERS == EXPECTED_SUPPLIERS
    assert _SYNC_SUPPLIERS == set(EXPECTED_SUPPLIERS)


def test_executable_adapters_use_the_approved_snapshot_builders():
    assert tuple(ADAPTERS) == (
        "cr_global", "sonara", "sunon", "alma", "lumbro", "jome", "lauco",
        "idelika", "conceptos",
    )
    assert ADAPTERS["idelika"].__name__ == "build_idelika_snapshot_with_assets"
    assert ADAPTERS["conceptos"].__name__ == "build_conceptos_snapshot_with_assets"
