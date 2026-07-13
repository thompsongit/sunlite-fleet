from pathlib import Path

from sunlite.maintenance import create_backup, restore_backup, verify_backup
from sunlite.storage import Repository


def test_backup_verify_and_restore(tmp_path: Path) -> None:
    database = tmp_path / "sunlite.db"
    Repository(database).migrate()
    config = tmp_path / "config.toml"
    config.write_text(
        (Path(__file__).parents[1] / "config.example.toml")
        .read_text()
        .replace(
            'database_path = "sunlite.db"',
            'database_path = "/var/lib/sunlite-scheduler/sunlite.db"',
        )
        .replace(
            'controller_socket = "/tmp/sunlite-controller.sock"',
            'controller_socket = "/run/sunlite-scheduler/controller.sock"',
        )
    )
    environment = tmp_path / "web.env"
    environment.write_text(
        "SUNLITE_ACCESS_REQUIRED=true\n"
        "SUNLITE_CF_TEAM_DOMAIN=https://lab.cloudflareaccess.com\n"
        "SUNLITE_CF_AUDIENCE=audience\n"
        f"SUNLITE_SESSION_SECRET={'s' * 32}\n"
        "SUNLITE_ADMIN_EMAILS=admin@example.com\n"
        "SUNLITE_ALLOWED_HOSTS=sunlite.example.com\n"
        "SUNLITE_CONTROLLER_SOCKET=/run/sunlite-scheduler/controller.sock\n"
    )
    archive = tmp_path / "backup.tar.gz"
    create_backup(database, config, environment, archive, "test-release")
    assert verify_backup(archive)["release"] == "test-release"

    config.write_text("broken")
    restore_backup(archive, database, config, environment)
    assert "Africa/Johannesburg" in config.read_text()
