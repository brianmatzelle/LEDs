SHELL := /bin/bash
VENV := .venv
PYTHON := $(VENV)/bin/python
CIRCUITPY := /run/media/cowboy/CIRCUITPY
SERIAL_PORT := /dev/ttyACM0
BAUD := 115200

.PHONY: help setup sim stream deploy deploy-file serial backup mount

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## Install Python venv and dependencies
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install pygame-ce pyserial
	@echo ""
	@echo "Setup complete. Activate with: source .venv/bin/activate"

sim: ## Run an app in the simulator only (make sim app=apps/rainbow.py)
	$(PYTHON) $(app)

stream: ## Run an app with simulator + board streaming (make stream app=apps/rainbow.py)
	$(PYTHON) $(app)

deploy: ## Deploy UDP receiver to board as code.py
	$(PYTHON) -m ledmatrix.deploy receiver

deploy-file: ## Deploy a specific file to board (make deploy-file file=board/receiver.py)
	$(PYTHON) -m ledmatrix.deploy $(file)

serial: ## Open serial console to the board
	@echo "Connecting to $(SERIAL_PORT) at $(BAUD) baud..."
	@echo "Press Ctrl+A then Ctrl+\\ to exit."
	@command -v picocom >/dev/null && picocom -b $(BAUD) $(SERIAL_PORT) || \
		(command -v screen >/dev/null && screen $(SERIAL_PORT) $(BAUD) || \
		echo "Install picocom or screen: pacman -S picocom")

backup: ## Backup current CIRCUITPY to board/backup/
	$(PYTHON) -m ledmatrix.deploy backup

mount: ## Mount CIRCUITPY if not already mounted
	@if [ ! -d "$(CIRCUITPY)" ]; then \
		udisksctl mount -b /dev/sdc1 2>/dev/null || \
		echo "Could not mount. Check lsblk for the correct device."; \
	else \
		echo "CIRCUITPY already mounted at $(CIRCUITPY)"; \
	fi
