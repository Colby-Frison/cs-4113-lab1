PYTHON = /home/student/lab1/.venv/bin/python
COVERAGE = $(PYTHON) -m coverage

STATEFUL = ../server-func-stateful/server_func.py
STATELESS = ../server-func-stateless/server_func.py

all:
	@echo "No compilation needed."

run-tests-coverage:
	$(COVERAGE) erase
	$(COVERAGE) run --append $(STATEFUL) --help
	$(COVERAGE) run --append $(STATELESS) --help
	$(COVERAGE) report -m --include="*/server-func-stateful/server_func.py,*/server-func-stateless/server_func.py"

clean:
	rm -f .coverage