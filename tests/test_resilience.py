import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))


class TestCircuitBreaker:
    def test_closed_state_normal(self):
        from resilience import CircuitBreaker, CircuitState
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=10)
        result = cb.call(lambda: 42)
        assert result == 42
        assert cb.state == CircuitState.CLOSED

    def test_open_after_threshold(self):
        from resilience import CircuitBreaker, CircuitState, CircuitBreakerOpen
        cb = CircuitBreaker("test", failure_threshold=3, recovery_timeout=60)

        def fail():
            raise ValueError("fail")

        for _ in range(3):
            try:
                cb.call(fail)
            except ValueError:
                pass
        assert cb.state == CircuitState.OPEN
        try:
            cb.call(lambda: 1)
            assert False, "Should have raised CircuitBreakerOpen"
        except CircuitBreakerOpen:
            pass

    def test_half_open_recovery(self):
        from resilience import CircuitBreaker, CircuitState
        import time
        cb = CircuitBreaker("test", failure_threshold=2, recovery_timeout=0, success_threshold=1)

        def fail():
            raise ValueError("fail")

        for _ in range(2):
            try:
                cb.call(fail)
            except ValueError:
                pass
        assert cb.state == CircuitState.OPEN
        time.sleep(0.1)
        result = cb.call(lambda: "recovered")
        assert result == "recovered"
        assert cb.state == CircuitState.CLOSED

    def test_retry_with_backoff_success(self):
        from resilience import retry_with_backoff
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise ValueError("not yet")
            return "ok"

        result = retry_with_backoff(flaky, max_retries=3, base_delay=0.01)
        assert result == "ok"
        assert len(attempts) == 3

    def test_retry_with_backoff_all_fail(self):
        from resilience import retry_with_backoff

        def always_fail():
            raise ValueError("always")

        try:
            retry_with_backoff(always_fail, max_retries=2, base_delay=0.01)
            assert False, "Should have raised"
        except ValueError:
            pass


class TestLifecycle:
    def test_import(self):
        from lifecycle import graceful_shutdown, on_shutdown, is_shutting_down
        assert callable(graceful_shutdown)
        assert callable(on_shutdown)
        assert is_shutting_down() == False

    def test_on_shutdown_register(self):
        from lifecycle import _cleanup_handlers, on_shutdown
        original = list(_cleanup_handlers)
        def handler():
            pass
        on_shutdown(handler)
        assert handler in _cleanup_handlers
        _cleanup_handlers.clear()
        _cleanup_handlers.extend(original)


class TestStorageCloseDb:
    def test_close_db(self):
        from storage import close_db, _db_conn
        # Should not crash even if not initialized
        close_db()
