export PYTHONPATH := src
export DATABASE_URL ?= postgresql://postgres:postgres@localhost:5432/metro

.PHONY: install db all build audit analyze test queries clean

install:        ## install Python dependencies
	pip install -r requirements.txt

db:             ## start PostgreSQL in Docker
	docker compose up -d --wait

all:            ## generate data, load, audit and analyse
	python -m metro_analytics all

build:
	python -m metro_analytics build

audit:
	python -m metro_analytics audit

analyze:
	python -m metro_analytics analyze

test:
	pytest -q

queries:        ## run the SQL business questions
	psql "$(DATABASE_URL)" -f sql/04_business_queries.sql

clean:
	rm -rf data/generated
