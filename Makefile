PROJECT := happyrobot-carrier-api
SERVICE := api

.PHONY: deploy redeploy env test

## First-time deploy: create the Railway project and service, copy .env, deploy, create a public URL.
deploy:
	@if railway status >/dev/null 2>&1; then \
		echo "This folder is already linked to a Railway project. Use 'make redeploy'."; exit 1; \
	fi
	railway init --name $(PROJECT)
	railway add --service $(SERVICE)
	python3 scripts/railway_env.py $(SERVICE)
	railway up --service $(SERVICE) --ci
	railway domain --service $(SERVICE)

## Deploy code changes to the existing Railway service.
redeploy:
	railway up --service $(SERVICE) --ci

## Copy changed .env values to Railway (then run 'make redeploy' to apply them).
env:
	python3 scripts/railway_env.py $(SERVICE)

## Run the API tests (FMCSA and the TMS are faked, so no network needed).
test:
	.venv/bin/python -m pytest -q tests
