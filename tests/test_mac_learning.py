"""Tests for mac_learning pure functions: extract_ipv6_suffix and fingerprint_similarity."""

import pytest
from presence.mac_learning import extract_ipv6_suffix, fingerprint_similarity, DeviceFingerprint


class TestExtractIpv6Suffix:
    def test_link_local(self):
        assert extract_ipv6_suffix("fe80::1a2b:3c4d:5e6f:7890") == "1a2b:3c4d:5e6f:7890"

    def test_link_local_with_zone_id(self):
        assert extract_ipv6_suffix("fe80::1a2b:3c4d:5e6f:7890%eth0") == "1a2b:3c4d:5e6f:7890"

    def test_full_ipv6_last_four_groups(self):
        result = extract_ipv6_suffix("2001:db8::1:2:3:4")
        assert result is not None
        assert "1:2:3:4" in result

    def test_none_on_empty(self):
        assert extract_ipv6_suffix("") is None

    def test_none_without_double_colon(self):
        # No '::' means we can't expand — function returns None
        assert extract_ipv6_suffix("2001:0db8:0000:0000:0000:0000:0000:0001") is None

    def test_none_on_none_input(self):
        assert extract_ipv6_suffix(None) is None


class TestFingerprintSimilarity:
    def test_identical_fingerprints(self):
        fp = DeviceFingerprint(device_type="iPhone", os_guess="iOS", ipv6_suffix="1:2:3:4")
        assert fingerprint_similarity(fp, fp) == pytest.approx(1.0)

    def test_empty_fingerprints_returns_zero(self):
        fp1 = DeviceFingerprint()
        fp2 = DeviceFingerprint()
        assert fingerprint_similarity(fp1, fp2) == pytest.approx(0.0)

    def test_device_type_mismatch(self):
        fp1 = DeviceFingerprint(device_type="iPhone")
        fp2 = DeviceFingerprint(device_type="Android")
        score = fingerprint_similarity(fp1, fp2)
        assert score == pytest.approx(0.0)

    def test_device_type_match(self):
        fp1 = DeviceFingerprint(device_type="iPhone")
        fp2 = DeviceFingerprint(device_type="iPhone")
        score = fingerprint_similarity(fp1, fp2)
        assert score == pytest.approx(1.0)

    def test_ipv6_suffix_match_is_high_weight(self):
        fp1 = DeviceFingerprint(ipv6_suffix="aa:bb:cc:dd", device_type="iPhone")
        fp2 = DeviceFingerprint(ipv6_suffix="aa:bb:cc:dd", device_type="iPhone")
        fp_no_ipv6 = DeviceFingerprint(device_type="iPhone")
        score_with = fingerprint_similarity(fp1, fp2)
        score_without = fingerprint_similarity(fp_no_ipv6, fp_no_ipv6)
        # Both should be 1.0 since all matching fields match, but ipv6 increases the total weight
        assert score_with == pytest.approx(1.0)
        assert score_without == pytest.approx(1.0)

    def test_ipv6_suffix_mismatch_lowers_score(self):
        fp1 = DeviceFingerprint(ipv6_suffix="aa:bb:cc:dd", device_type="iPhone")
        fp2 = DeviceFingerprint(ipv6_suffix="11:22:33:44", device_type="iPhone")
        score = fingerprint_similarity(fp1, fp2)
        # device_type matches (3.0/7.0), ipv6 mismatches (0/7.0) → 3/7
        assert score == pytest.approx(3.0 / 7.0)

    def test_os_guess_substring_match(self):
        fp1 = DeviceFingerprint(os_guess="iOS 17")
        fp2 = DeviceFingerprint(os_guess="iOS")
        score = fingerprint_similarity(fp1, fp2)
        assert score == pytest.approx(1.0)

    def test_open_ports_partial_overlap(self):
        ports1 = [{"port": 80, "protocol": "tcp"}, {"port": 443, "protocol": "tcp"}]
        ports2 = [{"port": 80, "protocol": "tcp"}, {"port": 22, "protocol": "tcp"}]
        fp1 = DeviceFingerprint(open_ports=ports1)
        fp2 = DeviceFingerprint(open_ports=ports2)
        score = fingerprint_similarity(fp1, fp2)
        # Intersection=1, union=3 → port_similarity=1/3
        assert score == pytest.approx(1.0 / 3.0)

    def test_score_bounded_zero_to_one(self):
        fp1 = DeviceFingerprint(
            device_type="iPhone", os_guess="iOS", ipv6_suffix="1:2:3:4",
            hostname_pattern="iphone", open_ports=[{"port": 80, "protocol": "tcp"}]
        )
        fp2 = DeviceFingerprint(
            device_type="Android", os_guess="Android", ipv6_suffix="9:9:9:9",
            hostname_pattern="android", open_ports=[{"port": 22, "protocol": "tcp"}]
        )
        score = fingerprint_similarity(fp1, fp2)
        assert 0.0 <= score <= 1.0
