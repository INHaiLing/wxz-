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
        if db_connection is not None:
            db_connection.cancel_safe(timeout=2)
        raise
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
