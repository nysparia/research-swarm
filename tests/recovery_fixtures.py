from research_swarm.provider_recovery import ProviderRecovery


class ImmediateRecovery(ProviderRecovery):
    """Advance a private clock; never wait real seconds in scripted HTTP tests."""
    def __init__(self):
        self.elapsed = 0.
        self.waits = []
        super().__init__(monotonic=lambda: self.elapsed, wall_time=lambda: 1700000000 + self.elapsed, jitter=lambda: 0.)
        self.condition.wait = self.advance

    def advance(self, timeout=None):
        self.waits.append(timeout)
        self.elapsed += timeout
