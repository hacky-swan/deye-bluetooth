"""Contract tests between deploy/deye_clock.yaml and the integration.

The automation reads entities, attributes, states and a service that the
integration publishes. Nothing else checks that those still line up: rename an
attribute, an entity or an outcome string and the YAML keeps parsing, keeps
running, and silently stops seeing anything — reporting every run as stale, or
never firing its alarm. That is the failure shape this whole feature keeps
producing, so it gets a test rather than a convention.

The entity IDs here were confirmed against the live entity registry for
sensor.deye_inverter_ble_inverter_clock_drift; the rest are derived the same way.
"""
from __future__ import annotations

import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")
pytest.importorskip("homeassistant")

from custom_components.deye_ble import const
from custom_components.deye_ble.sensor import DeyeClockSyncResultSensor

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "deye_clock.yaml"
RAW = PACKAGE.read_text(encoding="utf-8")
AUTOMATION = yaml.safe_load(RAW)["automation"][0]

# Mirrors homeassistant.util.slugify closely enough for these names: lowercase,
# runs of non-alphanumerics collapse to a single underscore, ends trimmed.
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _entity_id(domain: str, entity_name: str) -> str:
    return f"{domain}.{_slug(const.DEVICE_NAME)}_{_slug(entity_name)}"


class _Entry:
    data = {"logger_sn": "TESTSN123456"}


def _referenced(pattern: str) -> set[str]:
    return set(re.findall(pattern, RAW))


# --- Structure ---------------------------------------------------------------

def test_fires_daily_at_the_site_08_00():
    # 08:00 site time, which HA's own timezone resolves — that is what carries
    # the October DST step onto the inverter's timezone-less clock.
    assert AUTOMATION["trigger"] == [{"platform": "time", "at": "08:00:00"}]
    assert AUTOMATION["mode"] == "single"


def test_calls_the_service_the_integration_registers():
    init = (PACKAGE.parents[1] / "custom_components" / "deye_ble" / "__init__.py")
    registered = set(re.findall(r'async_register\(DOMAIN, "([a-z_]+)"', init.read_text(encoding="utf-8")))
    called = {
        step["action"].split(".", 1)[1]
        for step in AUTOMATION["action"]
        if isinstance(step, dict) and str(step.get("action", "")).startswith("deye_ble.")
    }
    assert called, "the automation calls no deye_ble service at all"
    assert called <= registered, f"calls unregistered service(s): {called - registered}"


def test_passes_a_drift_threshold_so_it_does_not_write_every_day():
    # Every sync is an opportunity to corrupt the RTC, so the daily run is a
    # CHECK that may or may not write. Calling the service by hand still syncs
    # unconditionally — the threshold lives here, not in the integration.
    call = next(
        s for s in AUTOMATION["action"]
        if isinstance(s, dict) and s.get("action") == "deye_ble.sync_clock"
    )
    assert isinstance(call["data"]["min_drift"], int)


# --- Contracts with the integration -----------------------------------------

def test_every_attribute_it_reads_is_one_the_sensor_publishes():
    """A renamed attribute would leave the automation reading None forever."""
    class _Coordinator:
        data = {
            "clock_sync_result": const.SYNC_OK,
            "clock_sync_id": 1,
            "clock_sync_attempts": 1,
            "clock_sync_at": None,
        }
        last_update_success = True

        def async_add_listener(self, *_a, **_kw):
            return lambda: None

    sensor = DeyeClockSyncResultSensor.__new__(DeyeClockSyncResultSensor)
    sensor.coordinator = _Coordinator()
    published = set(sensor.extra_state_attributes)

    read = _referenced(r"state_attr\('sensor\.[a-z0-9_]*clock_sync_result',\s*'([a-z_]+)'\)")
    assert read, "no sync_result attributes referenced — has the automation drifted?"
    assert read <= published, f"reads attributes the sensor does not publish: {read - published}"


def test_every_state_it_compares_against_is_a_real_outcome():
    """Renaming an outcome string would silence the alarm without a syntax error."""
    compared = _referenced(r"is_state\('sensor\.[a-z0-9_]*clock_sync_result',\s*'([a-z_]+)'\)")
    membership = re.search(r"clock_sync_result'\)\s*in\s*\[([^\]]+)\]", RAW)
    if membership:
        compared |= set(re.findall(r"'([a-z_]+)'", membership.group(1)))

    # Both forms must be covered, and both are used: is_state for the single
    # outcomes, a membership list for "ok or skipped counts as fine".
    assert compared == {const.SYNC_OK, const.SYNC_SKIPPED, const.SYNC_CLOCK_WRONG}, compared
    assert compared <= set(const.SYNC_RESULTS), (
        f"compares against outcomes the integration never publishes: "
        f"{compared - set(const.SYNC_RESULTS)}"
    )


def test_every_entity_it_reads_is_one_the_integration_creates():
    expected = {
        _entity_id("sensor", "Clock Sync Result"),
        _entity_id("sensor", "Inverter Clock Drift"),
        _entity_id("binary_sensor", "Cloud Clock Sync Enabled"),
    }
    referenced = _referenced(r"'((?:sensor|binary_sensor)\.deye[a-z0-9_]+)'")
    assert referenced, "no entities referenced"
    assert referenced <= expected, f"reads unknown entities: {referenced - expected}"


def test_the_alarm_can_actually_fire_for_every_failure_mode():
    # The recurring bug in this feature is a check that cannot report the thing
    # it exists to catch. Each signal must appear in the CONDITION that gates the
    # notify — not merely somewhere in the automation, which is what an earlier
    # version of this test checked. Every one of these names is also defined in
    # the variables block, so a substring search over the whole step passed even
    # with the condition gutted.
    conditions = [
        cond["value_template"]
        for step in AUTOMATION["action"] if isinstance(step, dict) and "if" in step
        for cond in step["if"] if "value_template" in cond
    ]
    assert conditions, "no conditional notify at all"
    gating = " ".join(conditions)
    for signal in ("stale", "sync_ok", "cloud_sync_off"):
        assert signal in gating, f"the alarm's condition ignores {signal}"
