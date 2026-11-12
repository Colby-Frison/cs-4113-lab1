PYTHON = /home/student/lab1/.venv/bin/python
COVERAGE = $(PYTHON) -m coverage

STATEFUL = ../server-func-stateful/server_func.py
STATELESS = ../server-func-stateless/server_func.py

all:
	@echo "No compilation needed."

run-tests-coverage:
	$(COVERAGE) erase
	$(COVERAGE) run --branch --append $(STATEFUL) --help
	$(COVERAGE) run --branch --append $(STATELESS) --help
	$(COVERAGE) run --branch --append -m unittest discover -s . -p "test_queue*.py" -v
	$(COVERAGE) run --branch --append ../queue/queue.py --help
		$(COVERAGE) run --branch --append -m unittest discover -s . -p "test_server_wrappers.py" -v
	$(COVERAGE) report -m --include="*/server-func-stateful/server_func.py,*/server-func-stateless/server_func.py,*/queue/queue.py"


clean:
	rm -f .coverage