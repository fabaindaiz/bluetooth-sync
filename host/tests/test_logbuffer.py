"""Búfer de logs del proceso (docs/research/09 §7.2)."""

import logging

import pytest

from aurasync.logbuffer import LogBuffer, service_of


@pytest.fixture
def buffer():
    buf = LogBuffer(capacity=5)
    logger = logging.getLogger("aurasync.test.buffer")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(buf)
    yield buf, logger
    logger.removeHandler(buf)


@pytest.mark.parametrize(
    ("name", "service"),
    [
        ("aurasync.svc.emitter", "emitter"),
        ("aurasync.svc.capture.child", "capture"),
        ("aurasync.panel", "panel"),
        ("aurasync.panel.server", "panel"),
        ("aurasync.engine", "motor"),
        ("aiohttp.access", "aiohttp"),
    ],
)
def test_service_comes_from_the_logger_name(name, service):
    assert service_of(name) == service


def test_records_keep_order_level_and_service(buffer):
    buf, logger = buffer
    logger.info("uno")
    logger.warning("dos %s", "con argumento")
    records = buf.tail(10)
    assert [r["message"] for r in records] == ["uno", "dos con argumento"]
    assert [r["level"] for r in records] == ["info", "warning"]
    assert records[1]["seq"] > records[0]["seq"]


def test_since_returns_only_newer_records(buffer):
    buf, logger = buffer
    logger.info("a")
    last = buf.last_seq
    logger.info("b")
    logger.info("c")
    assert [r["message"] for r in buf.since(last)] == ["b", "c"]
    assert buf.since(buf.last_seq) == []


def test_capacity_drops_the_oldest(buffer):
    buf, logger = buffer
    for i in range(8):
        logger.info("línea %d", i)
    assert [r["message"] for r in buf.tail(10)] == [f"línea {i}" for i in range(3, 8)]


def test_exceptions_keep_their_traceback(buffer):
    buf, logger = buffer

    def fail() -> None:
        msg = "falló"
        raise ValueError(msg)

    try:
        fail()
    except ValueError:
        logger.exception("algo salió mal")
    record = buf.tail(1)[0]
    assert record["level"] == "error"
    assert "ValueError: falló" in record["message"]
