"""
Build both personas against a real local Ollama and chat with one of them.

Usage:
    python demo.py mentor "Should I use REST or GraphQL?"
    python demo.py eli5 "What is an API?"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from ollama_client import OllamaClient, OllamaError
from persona_loader import load_persona_file

PERSONAS_DIR = Path(__file__).resolve().parent / "personas"

PERSONA_MAP = {
    "mentor": ("demo-no-nonsense-mentor", "no-nonsense-mentor.Modelfile"),
    "eli5": ("demo-eli5-explainer", "eli5-explainer.Modelfile"),
}


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in PERSONA_MAP:
        print(f"Usage: python demo.py <{'|'.join(PERSONA_MAP)}> \"your question\"")
        sys.exit(1)

    persona_key, question = sys.argv[1], sys.argv[2]
    model_name, filename = PERSONA_MAP[persona_key]

    client = OllamaClient()
    if not client.is_available():
        print("Ollama isn't reachable at localhost:11434. Start it with `ollama serve`.")
        sys.exit(1)

    persona = load_persona_file(PERSONAS_DIR / filename)
    print(f"Building '{model_name}' from {persona.base_model} ...")
    try:
        client.create_model(persona.to_create_payload(model_name))
    except OllamaError as e:
        print(f"Failed to create model: {e}")
        print(f"Did you run `ollama pull {persona.base_model}` first?")
        sys.exit(1)

    print(f"Asking {model_name}: {question}\n")
    reply = client.chat(model_name, [{"role": "user", "content": question}])
    print(reply)


if __name__ == "__main__":
    main()
