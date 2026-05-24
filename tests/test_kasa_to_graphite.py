"""Tests for kasa_to_graphite: resolve_device_ip and resolve_mac_to_ip."""

import subprocess
import pytest
import kasa_to_graphite as ktg


class TestResolveDeviceIpDispatch:
    def test_valid_ip_returned_directly(self):
        assert ktg.resolve_device_ip("192.168.1.50") == "192.168.1.50"

    def test_mac_address_dispatches_to_resolver(self, monkeypatch):
        monkeypatch.setattr(ktg, "resolve_mac_to_ip", lambda mac: "10.0.0.5")
        assert ktg.resolve_device_ip("aa:bb:cc:dd:ee:ff") == "10.0.0.5"

    def test_mac_address_dash_format_dispatches(self, monkeypatch):
        monkeypatch.setattr(ktg, "resolve_mac_to_ip", lambda mac: "10.0.0.6")
        assert ktg.resolve_device_ip("aa-bb-cc-dd-ee-ff") == "10.0.0.6"

    def test_hostname_dispatches_to_resolver(self, monkeypatch):
        monkeypatch.setattr(ktg, "resolve_hostname_to_ip", lambda h: "10.0.0.7")
        assert ktg.resolve_device_ip("mydevice.local") == "10.0.0.7"

    def test_unresolvable_hostname_returns_none(self, monkeypatch):
        monkeypatch.setattr(ktg, "resolve_hostname_to_ip", lambda h: None)
        assert ktg.resolve_device_ip("does-not-exist.local") is None


class TestResolveMacToIp:
    def _make_arp_result(self, stdout: str):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")

    def test_mac_found_in_arp(self, monkeypatch):
        arp_out = "? (192.168.1.42) at aa:bb:cc:dd:ee:ff [ether] on eth0\n"
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: self._make_arp_result(arp_out))
        assert ktg.resolve_mac_to_ip("aa:bb:cc:dd:ee:ff") == "192.168.1.42"

    def test_mac_normalization_uppercase(self, monkeypatch):
        arp_out = "? (192.168.1.10) at AA:BB:CC:DD:EE:FF [ether] on eth0\n"
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: self._make_arp_result(arp_out))
        assert ktg.resolve_mac_to_ip("aa:bb:cc:dd:ee:ff") == "192.168.1.10"

    def test_mac_not_in_arp_returns_none(self, monkeypatch):
        arp_out = "? (192.168.1.1) at 11:22:33:44:55:66 [ether] on eth0\n"
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: self._make_arp_result(arp_out))
        assert ktg.resolve_mac_to_ip("aa:bb:cc:dd:ee:ff") is None

    def test_subprocess_exception_returns_none(self, monkeypatch):
        def _raise(*a, **kw):
            raise OSError("arp not found")
        monkeypatch.setattr(subprocess, "run", _raise)
        assert ktg.resolve_mac_to_ip("aa:bb:cc:dd:ee:ff") is None
