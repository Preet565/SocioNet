.PHONY: test backend-test frontend-test build

test: backend-test frontend-test build

backend-test:
	$(MAKE) -C backend test

frontend-test:
	cd frontend && npm test

build:
	cd frontend && npm run build
