"""Validate integration metadata files parse correctly and contain required keys.

Catches CI-breaking issues (missing keys, bad JSON) before hassfest/HACS run.
"""
from __future__ import annotations

import json
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "deye_ble"

# hass.services.async_register(DOMAIN, "sync_clock", ...)
_REGISTERED = re.compile(r"""async_register\(\s*DOMAIN\s*,\s*["'](\w+)["']""")


def _load(path: pathlib.Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestManifest:
    def test_manifest_is_valid_json(self):
        data = _load(COMPONENT / "manifest.json")
        assert data["domain"] == "deye_ble"
        assert data["name"] == "Deye Bluetooth (Local)"
        assert data["codeowners"]
        assert data["config_flow"] is True
        assert data["iot_class"] == "local_polling"
        assert data["version"]

    def test_manifest_has_bluetooth_service(self):
        data = _load(COMPONENT / "manifest.json")
        ble = data.get("bluetooth")
        assert ble, "manifest.json must have a 'bluetooth' list"
        assert any(
            entry.get("service_uuid") for entry in ble
        ), "bluetooth entries must include service_uuid"


class TestServices:
    """services.yaml must describe every service the integration registers.

    It shipped missing entirely: HA logged "Failed to load services.yaml for
    integration: deye_ble" on every startup and both services appeared in
    Developer Tools with no fields, so calling sync_clock by hand meant typing
    raw YAML. Comparing against the registrations in __init__.py rather than a
    hard-coded list is what makes this fail when the NEXT service is added.
    """

    def test_every_registered_service_is_documented(self):
        source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
        registered = set(_REGISTERED.findall(source))
        assert registered, "no service registrations found — has the call moved?"

        with open(COMPONENT / "services.yaml", encoding="utf-8") as f:
            documented = yaml.safe_load(f)

        assert set(documented) == registered

    def test_sync_clock_documents_its_optional_threshold(self):
        # min_drift is the difference between "always set the clock" and "set it
        # only if it is far enough out". Undocumented, a caller cannot discover
        # that the bare call always writes.
        with open(COMPONENT / "services.yaml", encoding="utf-8") as f:
            documented = yaml.safe_load(f)

        field = documented["sync_clock"]["fields"]["min_drift"]
        assert field["required"] is False


class TestHacsJson:
    def test_hacs_is_valid_json(self):
        data = _load(ROOT / "hacs.json")
        assert data["name"] == "Deye Bluetooth (Local)"
        assert data["render_readme"] is True
        assert data["homeassistant"]
