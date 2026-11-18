from collections import deque


class SlidingWindowLimiter:
    def __init__(self, limit, window):
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be an integer >= 1")
        if isinstance(window, bool) or not isinstance(window, (int, float)) or window <= 0:
            raise ValueError("window must be a number > 0")
        self._limit = limit
        self._window = window
        self._events = {}
        self._last = {}

    def allow(self, key, now):
        last = self._last.get(key)
        if last is not None and now < last:
            raise ValueError("time went backwards for this key")
        self._last[key] = now
        events = self._events.setdefault(key, deque())
        while events and now - events[0] >= self._window:
            events.popleft()
        if len(events) < self._limit:
            events.append(now)
            return True
        return False
