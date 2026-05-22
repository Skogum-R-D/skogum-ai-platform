.PHONY: build-base build up down logs submit

build-base:
	docker build -t skogum-base:latest -f base/Dockerfile .

build: build-base
	docker-compose build

up: build-base
	docker-compose up -d

down:
	docker-compose down

logs:
	docker-compose logs -f

submit:
	python scripts/submit_assignment.py
