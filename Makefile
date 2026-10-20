.PHONY: test test-live backend-test frontend-test build

test: backend-test frontend-test build

# Optional cross-stack HTTP integration, using an isolated temporary database.
test-live:
	cd frontend && SOCIONET_LIVE_TEST=1 node --experimental-strip-types --test tests/liveWorkflow.test.mjs

backend-test:
	$(MAKE) -C backend test

frontend-test:
	cd frontend && npm test

build:
	cd frontend && npm run build
