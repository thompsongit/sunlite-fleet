import os
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_deployment_assets_are_safe_by_default() -> None:
    controller = (ROOT / "deploy/systemd/sunlite-controller.service").read_text()
    web = (ROOT / "deploy/systemd/sunlite-web.service").read_text()
    environment = (ROOT / "deploy/web.env.example").read_text()
    assert "User=sunlite-controller" in controller and "KillSignal=SIGINT" in controller
    assert "ProtectSystem=strict" in controller and "ProtectSystem=strict" in web
    assert "User=sunlite-web" in web and "ExecStart=" in web
    assert "SUNLITE_WEB_HOST=0.0.0.0" in environment
    assert all(os.access(path, os.X_OK) for path in (ROOT / "scripts").glob("*.sh"))
