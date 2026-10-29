from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[2]
SDK_ROOT = PROJECT_ROOT / "sdk"
GOLD_LEGACY_ROOT = Path(r"H:\UltimaAI\UltimaGold\ultima-pay")


def _retired_brand() -> str:
    return "tuppo" + "au"


def test_project_index_maps_standalone_service_authorities():
    index = (PROJECT_ROOT / "docs" / "INDEX.md").read_text(encoding="utf-8")
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "H:\\UltimaAI\\INDEX.md" in index
    assert "wo-ultimapay-20261029-0001--split-from-gold.md" in index
    assert "python run.py" in index
    assert "127.0.0.1:8061" in index
    assert "/api/pay/" in index
    assert "backend" in index and "sdk" in index and "tasks" in index
    assert "独立" in readme and "127.0.0.1:8061" in readme


def test_sdk_uses_standalone_base_url_and_canonical_routes():
    api = (SDK_ROOT / "pay-window" / "src" / "api.ts").read_text(encoding="utf-8")
    component = (
        SDK_ROOT / "pay-window" / "src" / "components" / "PayWindow.tsx"
    ).read_text(encoding="utf-8")
    vite = (SDK_ROOT / "pay-window" / "vite.config.ts").read_text(encoding="utf-8")

    assert "http://127.0.0.1:8061" in api
    assert "/api/pay" in api
    assert "getChannels" in api and "getChannels" in component
    assert "'/api/pay'" in vite
    assert "http://127.0.0.1:8061" in vite
    assert not (SDK_ROOT / "node_modules").exists()
    assert not (SDK_ROOT / "pay-window" / "node_modules").exists()


def test_live_sdk_and_migration_docs_have_no_retired_brand_or_path():
    retired = _retired_brand()
    old_payment_path = f"/api/{retired}/payment"
    checked = []
    for root in (SDK_ROOT, PROJECT_ROOT / "docs"):
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {
                ".md",
                ".ts",
                ".tsx",
                ".js",
                ".jsx",
                ".json",
                ".html",
            }:
                text = path.read_text(encoding="utf-8")
                assert retired not in text.lower(), path
                assert old_payment_path not in text.lower(), path
                checked.append(path)

    for name in ("README.md", "接入指南.md"):
        path = GOLD_LEGACY_ROOT / name
        text = path.read_text(encoding="utf-8")
        assert "H:\\UltimaAI\\UltimaPay" in text
        assert retired not in text.lower(), path
        assert old_payment_path not in text.lower(), path
        checked.append(path)

    assert checked
