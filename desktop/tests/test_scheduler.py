from komersia_desktop.scheduler import Scheduler


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(runner, clock, **kw):
    return Scheduler(runner=runner, clock=clock, interval=3600, initial_delay=60, **kw)


def test_runs_after_initial_delay_then_hourly():
    clock, calls = FakeClock(), []
    s = make(lambda name: calls.append(name) or True, clock)
    assert s.run_due() == [] and calls == []
    clock.now += 61
    assert s.run_due() == ["billing_tick", "backup_tick"]
    clock.now += 3599
    assert s.run_due() == []
    clock.now += 2
    assert s.run_due() == ["billing_tick", "backup_tick"]
    assert s.jobs["billing_tick"].runs == 2


def test_missing_command_is_ignored_without_error():
    clock = FakeClock()
    s = make(lambda name: name != "backup_tick", clock)
    clock.now += 100
    assert s.run_due() == ["billing_tick"]
    assert s.jobs["backup_tick"].skipped and s.jobs["backup_tick"].failures == 0


def test_failure_is_logged_retried_next_round_and_does_not_stop_others():
    clock, attempts = FakeClock(), {"billing_tick": 0}

    def runner(name):
        if name == "billing_tick":
            attempts[name] += 1
            if attempts[name] == 1:
                raise RuntimeError("boum")
        return True

    s = make(runner, clock)
    clock.now += 100
    assert s.run_due() == ["backup_tick"]
    assert s.jobs["billing_tick"].failures == 1
    clock.now += 3601
    assert "billing_tick" in s.run_due()


def test_clock_going_backwards_does_not_freeze_jobs():
    clock = FakeClock()
    s = make(lambda n: True, clock)
    clock.now += 100
    s.run_due()
    clock.now -= 10 * 3600  # l'horloge du PC recule de 10 heures
    s.run_due()
    assert s.seconds_until_next() <= 3600


def test_thread_start_stop_with_real_clock():
    ran = []
    s = Scheduler(runner=lambda n: ran.append(n) or True, initial_delay=0, interval=3600)
    s.start()
    for _ in range(50):
        if len(ran) >= 2:
            break
        s.stop_event.wait(0.1)
    s.stop()
    assert set(ran) == {"billing_tick", "backup_tick"}
