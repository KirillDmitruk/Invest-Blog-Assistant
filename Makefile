
# Invest Blog Assistant
# Development commands

.DEFAULT_GOAL := help

UV := uv
PYTHON := $(UV) run python

PORT ?= 8000
DAYS ?= 365
PARTIAL ?= 0

.PHONY: help install test bot web sync backfill \
        audit bonds risk performance \
        weekly monthly plan paper glossary \
        accounts check

# -------------------------
# Help
# -------------------------

help:
	@echo ""
	@echo "Invest Blog Assistant"
	@echo "-----------------------------"
	@echo "make install       - Install dependencies"
	@echo "make test          - Run tests"
	@echo "make check         - Run tests and audit"
	@echo "make bot           - Start Telegram bot"
	@echo "make web           - Start web dashboard"
	@echo "make sync          - Sync portfolio"
	@echo "make backfill      - Restore portfolio history"
	@echo "make audit         - Audit portfolio data"
	@echo "make bonds         - Bond analytics"
	@echo "make risk          - Portfolio risk analysis"
	@echo "make performance   - Portfolio performance"
	@echo "make accounts      - List brokerage accounts"
	@echo ""
	@echo "Post previews:"
	@echo "make weekly"
	@echo "make monthly"
	@echo "make plan"
	@echo "make paper"
	@echo "make glossary"
	@echo ""
	@echo "Options:"
	@echo "make web PORT=8080"
	@echo "make sync DAYS=30"
	@echo "make backfill DAYS=90 PARTIAL=1"
	@echo ""

# -------------------------
# Environment
# -------------------------

install:
	$(UV) sync --extra dev

# -------------------------
# Testing
# -------------------------

test:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 $(PYTHON) -m pytest -q

check: test audit

# -------------------------
# Application
# -------------------------

bot:
	$(PYTHON) -m invest_blog.cli bot

web:
	$(PYTHON) -m invest_blog.cli web --port $(PORT)

# -------------------------
# Portfolio
# -------------------------

sync:
	$(PYTHON) -m invest_blog.cli sync --days $(DAYS)

backfill:
	$(PYTHON) -m invest_blog.cli backfill --days $(DAYS) $(if $(filter 1,$(PARTIAL)),--partial,)

audit:
	$(PYTHON) -m invest_blog.cli audit

bonds:
	$(PYTHON) -m invest_blog.cli bonds

risk:
	$(PYTHON) -m invest_blog.cli risk

performance:
	$(PYTHON) -m invest_blog.cli performance

accounts:
	$(PYTHON) -m invest_blog.cli accounts

# -------------------------
# Post previews
# -------------------------

weekly:
	$(PYTHON) -m invest_blog.cli preview weekly

monthly:
	$(PYTHON) -m invest_blog.cli preview monthly

plan:
	$(PYTHON) -m invest_blog.cli preview plan

paper:
	$(PYTHON) -m invest_blog.cli preview paper

glossary:
	$(PYTHON) -m invest_blog.cli preview glossary
