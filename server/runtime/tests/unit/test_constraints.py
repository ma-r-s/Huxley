"""Contract tests for the named constraint snippets.

What the model does with these words can't be asserted here (that needs
a live voice session). What can be: the rules that fixed real failures
stay in the text, so a later edit can't silently drop them.
"""

from __future__ import annotations

import pytest

from huxley.constraints import CONSTRAINTS, UnknownConstraintError, compose


class TestEchoShortInput:
    @pytest.mark.parametrize(
        "command",
        ["pon la radio", "pon música", "pausa", "sigue", "ponlo", "siguiente", "más alto"],
    )
    def test_clear_short_commands_are_exempt(self, command: str) -> None:
        # "Pon radio" was echoed back as "Buscaste rock?" (2026-10-12).
        assert f"'{command}'" in CONSTRAINTS["echo_short_input"]

    def test_asking_for_something_by_name_is_a_clear_command(self) -> None:
        # "Pon Adele" is two words; echoing it would bring back the round
        # trip that confirm_if_unclear's best-guess rule removes.
        snippet = CONSTRAINTS["echo_short_input"]
        assert "'pon <nombre>'" in snippet
        assert "mejor interpretación" in snippet

    def test_answers_to_a_question_are_exempt(self) -> None:
        snippet = CONSTRAINTS["echo_short_input"]
        assert "'sí'" in snippet
        assert "'el segundo'" in snippet

    def test_silence_is_not_echoed_as_invented_words(self) -> None:
        # Presses with no speech got "Dijiste 'hola'?" seven times.
        snippet = CONSTRAINTS["echo_short_input"]
        assert "no inventes lo que dijo" in snippet


class TestConfirmIfUnclear:
    def test_reversible_actions_act_on_the_best_guess(self) -> None:
        snippet = CONSTRAINTS["confirm_if_unclear"]
        assert "se deshace fácil" in snippet
        assert "mejor interpretación" in snippet

    def test_irreversible_actions_still_ask_first(self) -> None:
        snippet = CONSTRAINTS["confirm_if_unclear"]
        assert "llega a otras personas" in snippet
        assert "NO llames ninguna herramienta" in snippet

    def test_a_correction_does_not_repeat_the_same_guess(self) -> None:
        assert "no repitas la misma suposición" in CONSTRAINTS["confirm_if_unclear"]


class TestCompose:
    def test_order_is_preserved(self) -> None:
        text = compose(["confirm_if_unclear", "echo_short_input"])
        assert text.index(CONSTRAINTS["confirm_if_unclear"]) < text.index(
            CONSTRAINTS["echo_short_input"]
        )

    def test_unknown_name_raises(self) -> None:
        with pytest.raises(UnknownConstraintError, match="nope"):
            compose(["nope"])
