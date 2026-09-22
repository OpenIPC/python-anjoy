"""ALARM_REPORT_MESSAGE push listener — CAPTURE-GATED (WIP).

Will subscribe to the camera's event/alarm stream over a dedicated
:mod:`anjoy.comm` connection (its own socket + auth + reader thread, like
python-dhip's ``EventListener``). Blocked on the binary-protocol capture — see
:mod:`anjoy.comm`.
"""

from __future__ import annotations


class EventListener:
    """Placeholder for the alarm-event listener (not yet implemented)."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "Event/alarm streaming rides the binary AJ protocol (anjoy.comm), "
            "which is capture-gated.")
