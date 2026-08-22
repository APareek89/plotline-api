PYTHON ?= /opt/homebrew/opt/python@3.12/bin/python3.12
VENV ?= ../.venv
RUN := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PORT ?= 8787

.PHONY: venv index sync sync-dry-run serve serve-offline serve-s3 eval test

venv: $(VENV)/.installed

$(VENV)/.installed: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e '.[test]'
	touch $(VENV)/.installed

index: venv
	$(RUN) -m plotline_rag.index.build build --input data/best_practice.jsonl --output .cache

sync: venv
	$(RUN) -m plotline_rag.index.build sync --input data/best_practice.jsonl --output .cache

sync-dry-run: venv
	$(RUN) -m plotline_rag.index.build sync --input data/best_practice.jsonl --output .cache --dry-run

serve: venv
	$(RUN) -m uvicorn plotline_rag.service:app --host 127.0.0.1 --port $(PORT)

serve-offline: venv
	PLOTLINE_OFFLINE=1 $(RUN) -m uvicorn plotline_rag.service:app --host 127.0.0.1 --port $(PORT)

serve-s3: venv
	PLOTLINE_INDEX_SOURCE=s3 $(RUN) -m uvicorn plotline_rag.service:app --host 127.0.0.1 --port $(PORT)

eval: venv
	$(RUN) evals/run_eval.py

test: venv
	AWS_EC2_METADATA_DISABLED=true PLOTLINE_OFFLINE=1 $(RUN) -m pytest

kb: venv
	$(RUN) ../kb/build_corpus.py

kb-check: venv
	$(RUN) ../kb/build_corpus.py --check
