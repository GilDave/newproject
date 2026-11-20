from solomon.quota import GUESS_RETRY_MIN, QuotaBoard


def test_a_guessed_reset_is_retried_soon_but_a_stated_reset_is_respected():
    b = QuotaBoard()
    b.mark("claude", "usage limit reached", now=1000.0, cooldown_min=120)  # the CLI did not say when
    assert b.exhausted("claude", 1000.0 + 60)
    assert b.exhausted("claude", 1000.0 + (GUESS_RETRY_MIN - 1) * 60)
    assert not b.exhausted("claude", 1000.0 + (GUESS_RETRY_MIN + 1) * 60) and "claude" not in b.snapshot(1000.0 + 3600)
    b.mark("codex", "limit|1900003600", now=1900000000.0)  # the CLI stated the reset time (epoch seconds)
    assert b.exhausted("codex", 1900000000.0 + 3000) and not b.exhausted("codex", 1900003700.0)
    b.mark("claude", "still limited", now=1000.0 + 2000)  # the retry failed: avoided again
    assert b.exhausted("claude", 1000.0 + 2000 + 60)
