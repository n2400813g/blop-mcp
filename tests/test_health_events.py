from datetime import datetime

from blop.engine.events import EventBus, HealthEvent


def test_event_bus_emits_events():
    bus = EventBus("run_001")
    ev = bus.emit("VALIDATE", "VALIDATE_START", "Starting validation")
    assert isinstance(ev, HealthEvent)
    assert ev.run_id == "run_001"
    assert ev.stage == "VALIDATE"
    assert ev.event_type == "VALIDATE_START"
    assert ev.seq == 1
    assert ev.message == "Starting validation"
    assert ev.details == {}
    assert isinstance(ev.timestamp, datetime)


def test_event_bus_seq_increments():
    bus = EventBus("run_002")
    bus.emit("VALIDATE", "VALIDATE_START", "a")
    ev2 = bus.emit("AUTH", "AUTH_START", "b")
    assert ev2.seq == 2


def test_event_bus_details_stored():
    bus = EventBus("run_003")
    ev = bus.emit("EXECUTE", "STEP_FAIL", "Step failed", {"step_index": 3, "selector": "button"})
    assert ev.details["step_index"] == 3
    assert ev.details["selector"] == "button"


def test_event_bus_events_returns_snapshot():
    bus = EventBus("run_004")
    bus.emit("VALIDATE", "VALIDATE_START", "a")
    bus.emit("VALIDATE", "VALIDATE_OK", "b")
    snapshot = bus.events
    assert len(snapshot) == 2
    # Mutating the snapshot does not affect internal list
    snapshot.clear()
    assert len(bus.events) == 2
