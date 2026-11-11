.PHONY: bench bench-all validate smoke-b smoke-c

CONFIG ?= A
TASKS ?= dw-001
RUNS ?= 1
MODEL ?=
JUDGE_MODEL ?=

bench:
	python3 -m src.runner \
		--config $(CONFIG) \
		--tasks $(TASKS) \
		--runs $(RUNS) \
		$(if $(MODEL),--model $(MODEL)) \
		$(if $(JUDGE_MODEL),--judge-model $(JUDGE_MODEL))

# Full benchmark: 12 tasks × 3 runs × 3 configs
bench-all:
	python3 -m src.runner --config A --tasks all --runs 3
	python3 -m src.runner --config B --tasks all --runs 3
	python3 -m src.runner --config C --tasks all --runs 3

# Smoke tests on dw-001
smoke-b:
	python3 -m src.runner --config B --tasks dw-001 --runs 1 --temperature 0.0

smoke-c:
	python3 -m src.runner --config C --tasks dw-001 --runs 1 --temperature 0.0

validate:
	python3 validate_tasks.py -v $(if $(TASK),$(TASK))
