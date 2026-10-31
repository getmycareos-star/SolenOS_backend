"""Low-overhead, content-free HTTP response timing middleware."""
from time import perf_counter


class LatencyTimingMiddleware:
    """Attach server processing time to HTTP response headers.

    The timer ends when response headers are emitted. For streaming responses,
    this intentionally does not claim to measure completion of the response body.
    No URL, user identifier, authorization token, or request/response body is logged.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()
        async def timed_send(message):
            if message.get("type") == "http.response.start":
                duration_ms = max(0.0, (perf_counter() - started) * 1000.0)
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"server-timing", f"app;dur={duration_ms:.2f}".encode("ascii")),
                    (b"x-response-time-ms", f"{duration_ms:.2f}".encode("ascii")),
                ])
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, timed_send)
