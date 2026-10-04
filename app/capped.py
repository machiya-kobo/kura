"""A cap on a listener's concurrent connections, and a limit on how long one may last.

The server starts a thread per connection. A client that opens many connections, or holds one open by sending a byte
at a time, would otherwise keep them all. With this mixin a connection over the cap is closed at once (a client sees a
reset and tries again later), and a connection that is still open after `lifetime` seconds is shut down. Put it before
the server class: `class Server(Capped, ThreadingHTTPServer)`. (The same as Niwa's `capped.py`.)"""
import socket
import threading


class Capped:
    max_connections = 64
    lifetime = 120          # seconds a connection may stay open, whatever it sends

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(self.max_connections)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:           # the thread didn't start: give the slot back
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        timer = threading.Timer(self.lifetime, self._expire, (request,))
        timer.daemon = True
        timer.start()
        try:
            super().process_request_thread(request, client_address)
        finally:
            timer.cancel()
            self._slots.release()

    @staticmethod
    def _expire(request):
        try:
            request.shutdown(socket.SHUT_RDWR)
        except (OSError, ValueError):
            pass
