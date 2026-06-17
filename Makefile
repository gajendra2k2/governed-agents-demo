.PHONY: install up down logs producer producer-fraud server agent agent-replay audit approve test clean

install:
	python -m pip install -e .

up:
	docker compose up -d
	@echo "Waiting for Kafka..."
	@until docker compose exec -T kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list >/dev/null 2>&1; do sleep 1; done
	@echo "Kafka ready."

down:
	docker compose down -v

logs:
	docker compose logs -f kafka

producer:
	python -m governed_agents.producer

producer-fraud:
	python -m governed_agents.producer --scenario fraud

server:
	python -m governed_agents.server.app

agent:
	python -m governed_agents.client.agent

agent-replay:
	python -m governed_agents.client.replay $(FILE)

audit:
	python scripts/audit_viewer.py

approve:
	@if [ -z "$(ID)" ]; then echo "Usage: make approve ID=<approval_id>"; exit 1; fi
	python scripts/approve.py $(ID)

test:
	pytest -q tests/

clean:
	rm -rf state/ transcripts/ __pycache__ src/governed_agents/__pycache__ src/governed_agents/server/__pycache__ src/governed_agents/client/__pycache__
