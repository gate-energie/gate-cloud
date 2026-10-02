"""ThingsFlow helpers that do not need a server."""
from types import SimpleNamespace

from gate_cloud.thingsflow import MAX_POINTS, ThingsFlow, chunks, parse_series
from tests.fakes import FakeThingsFlow, ref

MIN = 60_000


def test_one_day_of_minutes_is_one_request():
    assert chunks(0, 1440 * MIN, MIN, MAX_POINTS) == [(0, 1440 * MIN)]


def test_raw_points_over_the_cap_are_split():
    # 30 days at 15 min = 2880 buckets fits; 30 days of 1-min buckets does not
    parts = chunks(0, 30 * 1440 * MIN, MIN, MAX_POINTS)
    assert parts[0] == (0, MAX_POINTS * MIN) and parts[-1][1] == 30 * 1440 * MIN
    assert all(end - start <= MAX_POINTS * MIN for start, end in parts)


def test_parse_series_sorts_and_casts():
    raw = {"heating_active_power": [{"ts": 2, "value": "5.5"}, {"ts": 1, "value": "4"}]}
    assert parse_series(raw) == {"heating_active_power": [(1, 4.0), (2, 5.5)]}


def test_parse_series_drops_non_numeric():
    assert parse_series({"k": [{"ts": 1, "value": "on"}]}) == {"k": []}


def _fake_with_minutes():
    fake = FakeThingsFlow()
    fake.series[("a1", "p")] = [(i * MIN, float(i + 1)) for i in range(4)]
    return fake, ref("ASSET", "a1")


def test_fake_avg_buckets_like_the_server():
    fake, entity = _fake_with_minutes()
    out = fake.timeseries(entity, ["p"], 0, 4 * MIN, interval_ms=2 * MIN, agg="AVG")
    assert out == {"p": [(0, 1.5), (2 * MIN, 3.5)]}


def test_fake_buckets_are_aligned_to_the_epoch_not_to_start():
    # flow-core: date_bin('<interval> ms', ts) with no origin -> floor(ts / interval) * interval
    fake, entity = _fake_with_minutes()
    out = fake.timeseries(entity, ["p"], MIN, 4 * MIN, interval_ms=2 * MIN, agg="AVG")
    assert out == {"p": [(0, 2.0), (2 * MIN, 3.5)]}
    out = fake.timeseries(entity, ["p"], MIN, 4 * MIN, interval_ms=2 * MIN, agg="MAX")
    assert out == {"p": [(0, 2.0), (2 * MIN, 4.0)]}


def test_fake_max_buckets_and_single_point_without_interval():
    fake, entity = _fake_with_minutes()
    assert fake.timeseries(entity, ["p"], 0, 4 * MIN, interval_ms=2 * MIN, agg="MAX") == {"p": [(0, 2.0), (2 * MIN, 4.0)]}
    assert fake.timeseries(entity, ["p"], 0, 4 * MIN, agg="MAX") == {"p": [(0, 4.0)]}


class StubClient:
    """Stands in for RestClientCE so the wrapper builds real tb-rest-client models."""

    def __init__(self, devices=()):
        self.saved = []
        self._devices = list(devices)

    def get_tenant_devices(self, page_size, page):
        return SimpleNamespace(data=self._devices, has_next=False)

    def get_default_device_profile_info(self):
        return SimpleNamespace(id=SimpleNamespace(id="profile-default"))

    def save_device(self, body=None, access_token=None):
        self.saved.append(body)
        return SimpleNamespace(id=SimpleNamespace(id="device-new"))


def wrapper(client):
    session = ThingsFlow.__new__(ThingsFlow)
    session._client = client
    return session


def test_ensure_device_creates_with_the_default_device_profile():
    # tb-rest-client's Device validates device_profile_id in its constructor;
    # the fake never builds the model, so only this test can catch that.
    client = StubClient()
    assert wrapper(client).ensure_device("GATE Weather", "weather", "Weather") == "device-new"
    device = client.saved[0]
    assert (device.name, device.type, device.label) == ("GATE Weather", "weather", "Weather")
    assert device.device_profile_id.id == "profile-default"


def test_ensure_device_reuses_an_existing_device():
    existing = SimpleNamespace(name="GATE Weather", id=SimpleNamespace(id="device-old"))
    client = StubClient(devices=[existing])
    assert wrapper(client).ensure_device("GATE Weather", "weather", "Weather") == "device-old"
    assert client.saved == []
