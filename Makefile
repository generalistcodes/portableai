# Recurring PortableAI commands. `make` / `make help` lists them.
.DEFAULT_GOAL := help

PYTHON ?= $(if $(wildcard venv/bin/python),venv/bin/python,python3)
VERSION ?= dev

.PHONY: help test run fresh-install branch release release-test clean-tag icons appimage

help:
	@echo "PortableAI — common commands"
	@echo
	@echo "  make help                 Show this list (default)"
	@echo "  make test                 Run the test suite (pytest -v)"
	@echo "  make run                  Start the app (python run.py)"
	@echo "  make fresh-install        Wipe vendored Ollama + models, then start (first-run test)"
	@echo "  make branch NAME=x        Update main, create branch x, push it"
	@echo "  make release-test TAG=x   Create tag x and push it (opens the Actions URL to watch)"
	@echo "  make clean-tag TAG=x      Delete tag x locally and on origin (Release must be deleted in the GitHub UI)"
	@echo "  make icons                Regenerate platform icons from branding/logo-source.svg"
	@echo "  make appimage             Wrap dist/portableai with packaging/linux/make_appimage.sh"
	@echo "  make release VERSION=vX.Y.Z  Bump APP_VERSION, generate CHANGELOG.md, commit, tag, push"

test:
	$(PYTHON) -m pytest -v

run:
	$(PYTHON) run.py

fresh-install:
	@echo "Removing data/ollama-bin and data/ollama-models for a clean first-run..."
	rm -rf data/ollama-bin data/ollama-models
	$(PYTHON) run.py

branch:
	@test -n "$(NAME)" || { echo "NAME is required — example: make branch NAME=my-feature" >&2; exit 1; }
	git checkout main && git pull && git checkout -b "$(NAME)" && git push --set-upstream origin "$(NAME)"

release-test:
	@test -n "$(TAG)" || { echo "TAG is required — example: make release-test TAG=v0.7.0" >&2; exit 1; }
	git tag "$(TAG)"
	git push origin "$(TAG)"
	@url=$$(git remote get-url origin | sed -E 's#^git@github.com:#https://github.com/#; s#^ssh://git@github.com/#https://github.com/#; s#\.git$$##'); \
	echo "Pushed tag $(TAG). Check GitHub Actions:"; \
	echo "  $$url/actions"

clean-tag:
	@test -n "$(TAG)" || { echo "TAG is required — example: make clean-tag TAG=v0.7.0" >&2; exit 1; }
	git tag -d "$(TAG)"
	git push origin :refs/tags/$(TAG)
	@echo "If a GitHub Release was created for $(TAG), delete it in the GitHub UI — git cannot do that part."

icons:
	$(PYTHON) branding/generate_icons.py

appimage:
	VERSION=$(VERSION) bash packaging/linux/make_appimage.sh

release:
	@test -n "$(VERSION)" || { echo "VERSION is required — example: make release VERSION=v0.6.1" >&2; exit 1; }
	$(PYTHON) scripts/release.py "$(VERSION)"
