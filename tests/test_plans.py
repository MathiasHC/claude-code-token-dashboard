"""Choosing which subscription the api-equivalent cost is compared against."""

from __future__ import annotations

import json

import pytest

from dashboard import plans


def reader(*answers):
    """Feed scripted answers to plans.prompt(), then behave like EOF."""
    queued = list(answers)

    def read(_prompt=""):
        if not queued:
            raise EOFError
        return queued.pop(0)

    return read


def silent(_message=""):
    pass


# --- parsing ------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [("max-20x", 200.0), ("MAX-20X", 200.0), (" pro ", 20.0), ("api", 0.0)],
)
def test_parse_accepts_catalogue_keys(value, expected):
    assert plans.parse(value).monthly_usd == expected


@pytest.mark.parametrize("value,expected", [("149", 149.0), ("$149", 149.0), ("1,250.50", 1250.5)])
def test_parse_accepts_a_bare_monthly_amount(value, expected):
    """Annual billing, Team seats and negotiated rates aren't in the
    catalogue, so any amount has to be enterable directly."""
    assert plans.parse(value).monthly_usd == expected


@pytest.mark.parametrize("value", ["", "   ", "gold", "-20", "12x"])
def test_parse_rejects_nonsense(value):
    assert plans.parse(value) is None


# --- config file --------------------------------------------------------

def test_saved_plan_round_trips(tmp_path):
    path = tmp_path / "config.json"
    plans.save(plans.by_key("max-5x"), path)
    assert plans.load(path) == plans.by_key("max-5x")


def test_a_custom_amount_round_trips(tmp_path):
    path = tmp_path / "config.json"
    plans.save(plans.custom(149.0), path)
    assert plans.load(path).monthly_usd == 149.0


@pytest.mark.parametrize(
    "contents",
    ["{not json", "[]", '{"plan": "pro"}', '{"monthly_usd": "free"}', '{"monthly_usd": -5}'],
)
def test_a_corrupt_config_reads_as_absent(tmp_path, contents):
    """The config is a convenience cache. A bad one must fall back to asking
    or defaulting, never crash a wall display on startup."""
    path = tmp_path / "config.json"
    path.write_text(contents, encoding="utf-8")
    assert plans.load(path) is None


def test_a_missing_config_reads_as_absent(tmp_path):
    assert plans.load(tmp_path / "nope.json") is None


# --- resolution order ---------------------------------------------------

def test_explicit_argument_wins_and_is_remembered(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setenv(plans.ENV_VAR, "pro")
    plans.save(plans.by_key("max-5x"), path)
    chosen = plans.resolve("api", path=path, interactive=False, warn=silent)
    assert chosen.monthly_usd == 0.0
    assert plans.load(path).monthly_usd == 0.0, "an explicit choice should persist"


def test_environment_beats_the_saved_config(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    plans.save(plans.by_key("max-20x"), path)
    monkeypatch.setenv(plans.ENV_VAR, "pro")
    assert plans.resolve(path=path, interactive=False, warn=silent).monthly_usd == 20.0


def test_environment_does_not_overwrite_the_saved_config(tmp_path, monkeypatch):
    """A one-off env override shouldn't silently rewrite the stored answer."""
    path = tmp_path / "config.json"
    plans.save(plans.by_key("max-20x"), path)
    monkeypatch.setenv(plans.ENV_VAR, "pro")
    plans.resolve(path=path, interactive=False, warn=silent)
    assert plans.load(path).monthly_usd == 200.0


def test_saved_config_is_used_without_prompting(tmp_path, monkeypatch):
    monkeypatch.delenv(plans.ENV_VAR, raising=False)
    path = tmp_path / "config.json"
    plans.save(plans.custom(149.0), path)

    def explode(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("should not prompt when a plan is already saved")

    assert plans.resolve(path=path, interactive=True, read=explode).monthly_usd == 149.0


def test_an_invalid_explicit_argument_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="monthly amount"):
        plans.resolve("platinum", path=tmp_path / "c.json", interactive=False, warn=silent)


# --- the non-interactive path -------------------------------------------

def test_non_interactive_never_prompts_and_warns_instead(tmp_path, monkeypatch):
    """The load-bearing case: a systemd service or kiosk autostart has nobody
    to answer. Blocking there would leave the port unbound forever, which is
    far worse than comparing against the default."""
    monkeypatch.delenv(plans.ENV_VAR, raising=False)

    def explode(*_a, **_k):  # pragma: no cover - must never run
        raise AssertionError("prompted with no terminal attached")

    warnings = []
    chosen = plans.resolve(
        path=tmp_path / "config.json",
        interactive=False,
        read=explode,
        warn=warnings.append,
    )
    assert chosen == plans.DEFAULT
    assert warnings and plans.ENV_VAR in warnings[0]


def test_non_interactive_does_not_write_a_config(tmp_path, monkeypatch):
    """Falling back to a default is not the user answering the question, so
    it must not be recorded as their answer."""
    monkeypatch.delenv(plans.ENV_VAR, raising=False)
    path = tmp_path / "config.json"
    plans.resolve(path=path, interactive=False, warn=silent)
    assert not path.exists()


# --- the interactive prompt ---------------------------------------------

def test_prompt_selects_by_number():
    assert plans.prompt(read=reader("2"), write=silent) == plans.by_key("pro")


def test_prompt_accepts_a_key_instead_of_a_number():
    assert plans.prompt(read=reader("max-5x"), write=silent) == plans.by_key("max-5x")


def test_prompt_defaults_on_an_empty_answer():
    assert plans.prompt(read=reader(""), write=silent) == plans.DEFAULT


def test_prompt_asks_for_an_amount_on_the_last_option():
    chosen = plans.prompt(read=reader(str(len(plans.CATALOGUE) + 1), "149.50"), write=silent)
    assert chosen.monthly_usd == 149.5
    assert "149" in chosen.label


def test_prompt_reasks_after_a_bad_answer():
    assert plans.prompt(read=reader("platinum", "3"), write=silent) == plans.by_key("max-5x")


def test_prompt_defaults_when_the_input_stream_closes():
    """Ctrl-D at the prompt shouldn't traceback."""
    assert plans.prompt(read=reader(), write=silent) == plans.DEFAULT


def test_interactive_answer_is_saved(tmp_path, monkeypatch):
    monkeypatch.delenv(plans.ENV_VAR, raising=False)
    path = tmp_path / "config.json"
    plans.resolve(path=path, interactive=True, read=reader("3"), write=silent)
    assert json.loads(path.read_text(encoding="utf-8"))["monthly_usd"] == 100.0


def test_an_unwritable_config_does_not_break_startup(tmp_path, monkeypatch):
    """The answer is worth keeping but never worth failing the run over."""
    monkeypatch.delenv(plans.ENV_VAR, raising=False)
    blocked = tmp_path / "file"
    blocked.write_text("not a directory", encoding="utf-8")
    chosen = plans.resolve(
        path=blocked / "config.json", interactive=True, read=reader("2"), write=silent
    )
    assert chosen == plans.by_key("pro")
