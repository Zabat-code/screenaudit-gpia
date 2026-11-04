"""Informe #3: pause recording after N seconds without mouse/keyboard.

State machine with hysteresis: pause after `pause_after_seconds` of idle,
resume only after activity comes back AND idle drops below
`resume_after_seconds`. The two different thresholds stop the recorder from
flapping when someone brushes the mouse every few seconds.
"""
ACTIVE = "active"
PAUSED = "paused_idle"


class IdleGate:
    def __init__(self, pause_after=180.0, resume_after=5.0):
        self.pause_after = float(pause_after)
        self.resume_after = float(resume_after)
        self.state = ACTIVE
        self.paused_at = None

    def update(self, idle_s):
        """Feed the current idle reading. Returns the (possibly changed) state."""
        idle = float(idle_s)
        if self.state == ACTIVE:
            if idle >= self.pause_after:
                self.state = PAUSED
                self.paused_at = idle
        else:
            if idle <= self.resume_after:
                self.state = ACTIVE
                self.paused_at = None
        return self.state

    @property
    def is_paused(self):
        return self.state == PAUSED
