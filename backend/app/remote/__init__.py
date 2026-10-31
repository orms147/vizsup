"""Remote control channels ("Điều khiển từ xa"): the owner talks to vizsup from the phone.

A channel turns its platform's updates into ``Event``s (only the paired owner's — anything
else is dropped there, without a reply) and sends plain-text messages with buttons. What the
events MEAN — queue a link, approve an episode behind the revision guard, the status page —
lives in app/pipeline/remote.py and is the same for every channel. Telegram is the only one
(``telegram.py``); another (Zalo, a Google Sheet) would be one module with the same calls:

    configured() / paired() / notify_on(kind)
    poll(timeout) -> raw updates (no state change)      update_id(raw) / commit(update_id)
    accept(raw) -> Event | None   (auth + pairing)      send(text, buttons) -> message id
    ack(event, text)              (a button press)      drop_buttons(event)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Event:
    """One accepted update from the paired owner. ``kind``: "text" (a message), "button" (an
    inline button pressed; ``data`` = its payload, ``ref`` = the press to acknowledge,
    ``message_id`` = the message holding the button) or "paired" (pairing just succeeded;
    ``who`` = the owner's display name)."""
    kind: str
    text: str = ""
    data: str = ""
    ref: str = ""
    message_id: int | None = None
    who: str = ""
    uid: int = 0


@dataclass
class Button:
    text: str
    data: str


def channel():
    """The configured remote channel (a bot token saved), else None."""
    from app.remote import telegram

    ch = telegram.TelegramChannel()
    return ch if ch.configured() else None
