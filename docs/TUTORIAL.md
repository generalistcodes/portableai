# Tutorial

This started as a tutorial on GGUF/Ollama and model personas. A persona
in Ollama is just a base GGUF model plus a fixed system prompt and a few
parameters, packaged into a named model. This repo shows how to build
one, drive it from Python instead of the CLI, and — the part most
tutorials skip — how to actually test it.

Running the server and the chat UI is covered in the
[README](../README.md) quick start and in [Development](DEVELOPMENT.md).

## The two ways to build a persona

**The CLI way** (what most tutorials show you):

```bash
ollama create no-nonsense-mentor -f personas/no-nonsense-mentor.Modelfile
ollama run no-nonsense-mentor "Should I use REST or GraphQL for a small API?"
```

**The programmatic way** (what this repo actually tests):

```bash
python demo.py mentor "Should I use REST or GraphQL for a small API?"
```

Under the hood, `demo.py` parses the same `.Modelfile` text with
`persona_loader.parse_modelfile()`, converts it into the JSON body
`/api/create` expects, and POSTs it via `OllamaClient`. Same result, but
now it's code you can unit test.

## Example prompts to try

New chats land on **Assistant** with no special instructions. These are
the kind of ordinary questions people actually type first:

**assistant** (the default)
- "Summarize the difference between TCP and UDP in a few sentences."
- "Help me write a polite follow-up email after an interview."

The named personas are optional — switch in the sidebar when you want a
specific voice. These make their differences obvious in a single reply:

**no-nonsense-mentor**
- "Should I use REST or GraphQL for a small internal API?"
- "I think I broke my database migration, help."
- "Give me a code review checklist for a pull request."

**eli5-explainer**
- "What is an API?"
- "How does a blockchain work?"
- "Why does my laptop get hot when I play games?"

Worth trying the same question against Mentor and Explainer back-to-back
(switch in the sidebar, keep the question identical) — that side-by-side
contrast is usually the most convincing part of a persona demo.

## Extending this

- Add a persona: drop a new `.Modelfile` in `personas/`, add a
  `test_bundled_personas_parse_cleanly` case, done — the loader and client
  don't change.
- Swap the base model: change `FROM llama3.2:3b` to any model you've
  pulled; nothing else in the repo cares what's underneath.
- LoRA fine-tunes: `ADAPTER` is a valid Modelfile directive not yet
  handled by `persona_loader.py` — that's the natural "part 2" of this
  tutorial.
