from types import SimpleNamespace

import engine.bot.telegram as tg
from engine.bot.telegram import _default_orders_venue, _resolve_operator_venue


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


def _install_fake_bot(monkeypatch) -> _FakeBot:
    bot = _FakeBot()
    monkeypatch.setattr(tg, "_app", SimpleNamespace(bot=bot))
    monkeypatch.setattr(tg, "_settings", SimpleNamespace(telegram_chat_id="123"))
    tg._recent_alerts.clear()
    return bot


async def test_forward_alert_suppresses_telegram_when_skip_flag_set(monkeypatch):
    bot = _install_fake_bot(monkeypatch)

    await tg.forward_alert(
        {"severity": "warning", "message": "delta drift", "skip_telegram": True}
    )

    assert bot.sent == []


async def test_forward_alert_sends_warning_without_skip_flag(monkeypatch):
    bot = _install_fake_bot(monkeypatch)

    await tg.forward_alert({"severity": "warning", "message": "delta drift"})

    assert len(bot.sent) == 1
    assert "delta drift" in bot.sent[0][1]


def test_default_orders_venue_prefers_quidax_lp_for_order_views():
    trade = object()
    lp = object()
    runtime = SimpleNamespace(venues={"quidax": trade, "quidax-lp": lp})

    venue_name, venue = _default_orders_venue(runtime)

    assert venue_name == "quidax-lp"
    assert venue is lp


def test_operator_venue_resolution_keeps_quidax_trade_exact_when_lp_exists():
    trade = object()
    lp = object()
    runtime = SimpleNamespace(venues={"quidax": trade, "quidax-lp": lp})

    venue_name, venue = _resolve_operator_venue(runtime, "quidax")

    assert venue_name == "quidax"
    assert venue is trade
