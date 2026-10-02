"""Run PostgreSQL transaction assertions on a genuinely separate connection."""

from concurrent.futures import ThreadPoolExecutor

from django.db import connections


def on_independent_connection(operation):
    worker = {}

    def run():
        # Django connections are thread-local. Close even when an assertion fails.
        try:
            db = connections["default"]
            db.ensure_connection()
            worker["connection"] = db.connection
            return operation()
        finally:
            connections.close_all()

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(run)
    try:
        return future.result(timeout=10)
    except TimeoutError:
        # psycopg cancellation is safe from another thread. SQL itself is also
        # bounded by the PostgreSQL test settings; don't wait again on shutdown.
        db_connection = worker.get("connection")
        cancel = getattr(db_connection, "cancel_safe", None)
        if not future.done() and callable(cancel):
            try:
                cancel(timeout=2)
            except Exception:
                # Cancellation is cleanup, and must not hide the test timeout.
                pass
        raise
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
