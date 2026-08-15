PYTHON ?= python3
SCRIPTS := scripts

.DEFAULT_GOAL := help
.PHONY: help all fetch transform tile validate validate-offline validate-deep publish serve clean check-tools

help: ## Show available targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk -F':.*?## ' '{printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'

check-tools: ## Verify ogr2ogr and tippecanoe are on PATH
	@command -v ogr2ogr   >/dev/null || { echo "missing: ogr2ogr (GDAL)";   exit 1; }
	@command -v tippecanoe >/dev/null || { echo "missing: tippecanoe";       exit 1; }
	@echo "ogr2ogr    $$(ogr2ogr --version)"
	@echo "tippecanoe $$(tippecanoe --version 2>&1 | head -1)"

all: fetch transform tile validate publish ## Full pipeline end to end

fetch: check-tools ## Validate the WFS and download the parcel layer
	$(PYTHON) $(SCRIPTS)/fetch.py

transform: ## Reproject to WGS84 and classify ownership
	$(PYTHON) $(SCRIPTS)/transform.py

tile: ## Build the PMTiles archive
	$(PYTHON) $(SCRIPTS)/tile.py

validate: ## Run the per-build guards (source + contract + header)
	$(PYTHON) $(SCRIPTS)/validate.py

validate-offline: ## Per-build guards without the network cross-check
	$(PYTHON) $(SCRIPTS)/validate.py --skip-network

validate-deep: ## Also decode every tile; run when tippecanoe/GDAL/config change
	$(PYTHON) $(SCRIPTS)/validate.py --deep

publish: ## Assemble dist/ with an immutable tileset path and manifest
	$(PYTHON) $(SCRIPTS)/publish.py

serve: ## Serve dist/ locally (range-capable; stdlib http.server is not)
	$(PYTHON) $(SCRIPTS)/serve.py --dir dist

clean: ## Remove build and dist artefacts
	rm -rf build dist
