import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from persona_loader import ModelfileParseError, Persona, load_persona_file, parse_modelfile

PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"


def test_parses_from_line():
    persona = parse_modelfile("FROM llama3.2:3b\n")
    assert persona.base_model == "llama3.2:3b"


def test_missing_from_raises():
    with pytest.raises(ModelfileParseError, match="FROM"):
        parse_modelfile('SYSTEM "hello"\n')


def test_single_line_system_quoted():
    text = 'FROM llama3.2:3b\nSYSTEM "You are terse."\n'
    persona = parse_modelfile(text)
    assert persona.system == "You are terse."


def test_triple_quoted_system_block():
    text = (
        "FROM llama3.2:3b\n"
        'SYSTEM """\n'
        "Line one.\n"
        "Line two.\n"
        '"""\n'
    )
    persona = parse_modelfile(text)
    assert persona.system == "Line one.\nLine two."


def test_unterminated_triple_quote_raises():
    text = 'FROM llama3.2:3b\nSYSTEM """\nno closing quotes here\n'
    with pytest.raises(ModelfileParseError, match="unterminated"):
        parse_modelfile(text)


def test_numeric_parameters_are_cast():
    text = "FROM llama3.2:3b\nPARAMETER temperature 0.2\nPARAMETER num_ctx 4096\n"
    persona = parse_modelfile(text)
    assert persona.parameters["temperature"] == pytest.approx(0.2)
    assert persona.parameters["num_ctx"] == 4096
    assert isinstance(persona.parameters["num_ctx"], int)


def test_malformed_numeric_parameter_raises():
    text = "FROM llama3.2:3b\nPARAMETER temperature not-a-number\n"
    with pytest.raises(ModelfileParseError, match="temperature"):
        parse_modelfile(text)


def test_malformed_parameter_line_raises():
    text = "FROM llama3.2:3b\nPARAMETER onlyonetoken\n"
    with pytest.raises(ModelfileParseError, match="malformed PARAMETER"):
        parse_modelfile(text)


def test_comments_and_blank_lines_ignored():
    text = "# a comment\n\nFROM llama3.2:3b\n\n# another\nPARAMETER temperature 0.5\n"
    persona = parse_modelfile(text)
    assert persona.base_model == "llama3.2:3b"
    assert persona.parameters["temperature"] == 0.5


def test_windows_line_endings_parse_the_same_as_unix():
    """A Modelfile edited or checked out on Windows may have CRLF line
    endings; parsing must not choke on or leak the \\r into values."""
    crlf_text = 'FROM llama3.2:3b\r\nPARAMETER temperature 0.2\r\nSYSTEM "Be terse."\r\n'
    persona = parse_modelfile(crlf_text)
    assert persona.base_model == "llama3.2:3b"
    assert persona.parameters["temperature"] == pytest.approx(0.2)
    assert persona.system == "Be terse."
    assert "\r" not in persona.system


def test_to_create_payload_shape():
    persona = Persona(base_model="llama3.2:3b", system="Be terse.", parameters={"temperature": 0.2})
    payload = persona.to_create_payload("my-persona")
    assert payload == {
        "model": "my-persona",
        "from": "llama3.2:3b",
        "system": "Be terse.",
        "parameters": {"temperature": 0.2},
    }


def test_to_create_payload_requires_name():
    persona = Persona(base_model="llama3.2:3b")
    with pytest.raises(ModelfileParseError):
        persona.to_create_payload("")


def test_load_persona_file_missing_raises():
    with pytest.raises(FileNotFoundError):
        load_persona_file(PERSONAS_DIR / "does-not-exist.Modelfile")


@pytest.mark.parametrize(
    "filename",
    ["no-nonsense-mentor.Modelfile", "eli5-explainer.Modelfile"],
)
def test_bundled_personas_parse_cleanly(filename):
    persona = load_persona_file(PERSONAS_DIR / filename)
    assert persona.base_model == "llama3.2:3b"
    assert persona.system  # non-empty
    assert "temperature" in persona.parameters
