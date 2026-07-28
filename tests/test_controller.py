import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sunlite.clock import FakeClock
from sunlite.codec import schedule_to_dict
from sunlite.config import load_config
from sunlite.controller import ControllerService
from sunlite.domain import (
    CommandedState,
    OnDemandSchedule,
    RecoveryPolicy,
    RegularSchedule,
    ScheduleStep,
)
from sunlite.ipc import ControllerClient, ControllerSocketServer
from sunlite.relay import RecordingRelay
from sunlite.storage import Repository

NOW = datetime(2026, 7, 12, 8, tzinfo=UTC)


def make_schedule(
    schedule_id: str, device_id: str, starts_at: datetime, recovery: RecoveryPolicy
) -> RegularSchedule:
    return RegularSchedule(
        schedule_id,
        device_id,
        schedule_id,
        starts_at,
        "Africa/Johannesburg",
        timedelta(minutes=10),
        timedelta(minutes=5),
        repeat_count=2,
        recovery_policy=recovery,
    )


def test_controller_reconciles_devices_and_latched_stop(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "config.example.toml")
    clock = FakeClock(NOW)
    repository = Repository(tmp_path / "controller.db")
    relay = RecordingRelay(tuple(device.id for device in config.devices))
    controller = ControllerService(config, repository, relay, clock)
    controller.initialize()

    schedule = make_schedule(
        "cycle-a", "sunlite-a", NOW + timedelta(minutes=1), RecoveryPolicy.ABORT_IF_INTERRUPTED
    )
    controller.handle({"action": "schedules.save", "schedule": schedule_to_dict(schedule)})
    clock.advance(timedelta(minutes=1))
    status = controller.reconcile()
    devices = {item["id"]: item for item in status["devices"]}
    assert devices["sunlite-a"]["commanded_state"] == "on"
    assert devices["sunlite-b"]["commanded_state"] == "off"
    assert controller.next_boundary() == schedule.starts_at + timedelta(minutes=10)

    stop = {
        "action": "stop_all",
        "actor": "operator",
        "idempotency_key": "controller-test-stop-0001",
    }
    controller.handle(stop)
    controller.handle(stop)
    assert relay.state("sunlite-a") is CommandedState.OFF
    assert repository.load_global_stop() is True
    saved = repository.get_schedule(schedule.id)
    assert saved is not None and saved.enabled is False
    assert repository.record_counts()["commands"] == 2


def test_recovery_and_unix_socket_round_trip(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = load_config(Path(__file__).parents[1] / "config.example.toml")
        repository = Repository(tmp_path / "recovery.db")
        repository.migrate()
        repository.save_registry(config)
        started = NOW - timedelta(minutes=2)
        repository.save_schedule(
            make_schedule("abort-a", "sunlite-a", started, RecoveryPolicy.ABORT_IF_INTERRUPTED)
        )
        repository.save_schedule(
            make_schedule("resume-b", "sunlite-b", started, RecoveryPolicy.RESUME_IF_ACTIVE)
        )
        relay = RecordingRelay(tuple(device.id for device in config.devices))
        controller = ControllerService(config, repository, relay, FakeClock(NOW))
        controller.initialize()
        aborted = repository.get_schedule("abort-a")
        assert aborted is not None and aborted.enabled is False
        assert relay.state("sunlite-b") is CommandedState.ON

        socket = Path.cwd() / f".sunlite-{uuid4().hex[:8]}.sock"
        server = ControllerSocketServer(controller, socket)
        await server.start()
        serving = asyncio.create_task(server.serve_forever())
        try:
            status = await ControllerClient(socket).request({"action": "status"})
            assert status["healthy"] is True
            assert len(status["devices"]) == 3
        finally:
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
            await server.close()

    asyncio.run(scenario())


def test_on_demand_launch_counts_handoff_before_ocp_run(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "config.example.toml")
    clock = FakeClock(NOW)
    repository = Repository(tmp_path / "on-demand.db")
    relay = RecordingRelay(tuple(device.id for device in config.devices))
    controller = ControllerService(config, repository, relay, clock)
    controller.initialize()
    plan = OnDemandSchedule(
        "ocp-a",
        "sunlite-a",
        "OCP stability",
        "Africa/Johannesburg",
        (
            ScheduleStep(timedelta(seconds=60), CommandedState.ON),
            ScheduleStep(timedelta(seconds=75), CommandedState.OFF),
        ),
        handoff_delay=timedelta(seconds=4),
        ocp_duration=timedelta(seconds=60),
    )
    controller.handle(
        {
            "action": "schedules.save",
            "schedule": schedule_to_dict(plan),
            "idempotency_key": "save-on-demand-plan-0001",
        }
    )
    status = controller.handle(
        {
            "action": "schedules.launch",
            "id": plan.id,
            "handoff_seconds": 4,
            "ocp_seconds": 60,
            "idempotency_key": "launch-on-demand-0001",
        }
    )
    assert status["devices"][0]["phase"] == "handoff"
    clock.advance(timedelta(seconds=4))
    assert controller.reconcile()["devices"][0]["phase"] == "ocp"
    assert repository.list_runs()[0].actual_start == NOW + timedelta(seconds=4)
    clock.advance(timedelta(seconds=60))
    controller.reconcile()
    assert relay.state("sunlite-a") is CommandedState.ON
