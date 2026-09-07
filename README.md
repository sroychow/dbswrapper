# dbs2go-wrapper

A small, read-only Linux command-line wrapper for the CMS DBS Reader/dbs2go HTTP APIs.
It searches for datasets and writes dataset information and metadata to organized JSON files.

This repository is intended as the first building block for a later local cache and web UI.

## What it dumps

For each matching dataset, the `dump` command queries:

- `datasets` — detailed dataset metadata
- `filesummaries` — file, event, block, lumi, and size summary
- `runs` — associated runs
- `blocks` — block metadata
- `outputconfigs` — processing configuration and CMSSW global tag
- `blocklocations` — replica sites for every dataset block, summarized as site and replication metrics
- `datasetparents` — parent datasets
- `datasetchildren` — child datasets
- `files` — optional full file metadata
- `hierarchy` — optional recursive parent and child dataset tree

Only read-only DBS endpoints are allowed by the client.

## Requirements

- Linux
- Python 3.9 or newer
- A valid CMS X.509 proxy, or a certificate/key pair
- Network access to CMSWEB

## Installation

```bash
cd dbswrapper
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Verify the installation:

```bash
dbs2go-json --version
dbs2go-json --help
```

You can also run directly from the repository without installing the package, provided `requests` is available:

```bash
./bin/dbs2go-json --help
```

## X.509 setup

The easiest method is to create a CMS proxy:

```bash
voms-proxy-init -voms cms -valid 24:00
export X509_USER_PROXY="$(voms-proxy-info -path)"
export X509_CERT_DIR=/cvmfs/grid.cern.ch/etc/grid-security/certificates
```

The program searches for credentials in this order:

1. `--proxy`
2. `--cert` and `--key`
3. `X509_USER_PROXY`
4. `/tmp/x509up_u$(id -u)`
5. `X509_USER_CERT` and `X509_USER_KEY`
6. `~/.globus/usercert.pem` and `~/.globus/userkey.pem`

The private key or proxy is never copied into the output directory.

## Search for datasets

```bash
dbs2go-json search '/Muon/Run2024*/MINIAOD' --names-only --ca-bundle "$X509_CERT_DIR"
```

Return detailed search results as JSON:

```bash
dbs2go-json search '/Muon/Run2024*/MINIAOD' \
  --access-type VALID \
  --output search.json \
  --ca-bundle "$X509_CERT_DIR"
```

Add any supported DBS dataset parameter:

```bash
dbs2go-json search '/*/*/NANOAODSIM' \
  --param acquisition_era_name=Run3Summer24* \
  --param physics_group_name=Higgs \
  --output search.json \
  --ca-bundle "$X509_CERT_DIR"
```

## Dump dataset information

Dump an exact dataset:

```bash
dbs2go-json dump \
  '/Muon/Run2024C-PromptReco-v1/MINIAOD' \
  --output output \
  --ca-bundle "$X509_CERT_DIR"
```

Dump every dataset matching a wildcard:

```bash
dbs2go-json dump \
  '/Muon/Run2024*/MINIAOD' \
  --output output \
  --workers 4 \
  --max-datasets 200 \
  --ca-bundle "$X509_CERT_DIR"
``` 

Include full file metadata:

```bash
dbs2go-json dump \
  '/Muon/Run2024C-PromptReco-v1/MINIAOD' \
  --output output \
  --include-files \
  --ca-bundle "$X509_CERT_DIR"
```

`--include-files` can create a large JSON file for large datasets, so it is disabled by default.

Dump every optional artifact, including full metadata for valid and invalid files and the complete
parent/child hierarchy:

```bash
dbs2go-json dump '/Muon/Run2024C-PromptReco-v1/MINIAOD' --all --output output \
  --ca-bundle "$X509_CERT_DIR"
```

`--all` is equivalent to combining `--include-files`, `--all-files`, and `--include-hierarchy`.

Include the complete parent and child hierarchy of each dumped dataset:

```bash
dbs2go-json dump \
  '/Muon/Run2024C-PromptReco-v1/MINIAOD' \
  --include-hierarchy \
  --output output \
  --ca-bundle "$X509_CERT_DIR"
```

Every dump also writes `cache.json` in the directory where the command is run. It maps each
successfully dumped dataset to its output directory and fetch time; use `--cache PATH` to choose
another location.

This writes `hierarchy.json` and includes it in `bundle.json`. The hierarchy is a tree rooted at
the requested dataset: each parent recursively contains its parents and each child recursively
contains its children. Repeated relationships are marked with `"cycle": true` rather than being
followed indefinitely.

## Output layout

```text
output/
├── manifest.json
├── search_results.json
# cache.json is written in the command's working directory by default
└── datasets/
    └── Muon/
        └── Run2024C-PromptReco-v1/
            └── MINIAOD/
                ├── bundle.json
                ├── metadata.json
                ├── summary.json
                ├── runs.json
                ├── blocks.json
                ├── output_configs.json
                ├── configuration.json
                ├── site_replicas.json
                ├── parents.json
                ├── children.json
                ├── hierarchy.json      # only with --include-hierarchy
                └── files.json          # only with --include-files
```

`bundle.json` contains all sections in one file. Its top-level `configuration` field contains the
raw DBS output configuration records and their unique CMSSW `global_tags`. The `site_replicas`
section lists every replica site by block, per-site file and block coverage fractions, and the
fractions of dataset blocks and files replicated to more than one site. The individual files make
later database ingestion simpler.

## Run an individual DBS Reader query

```bash
dbs2go-json query filesummaries \
  --param dataset=/Muon/Run2024C-PromptReco-v1/MINIAOD \
  --param validFileOnly=1 \
  --output summary.json
```

Multiple values for the same parameter are supported:

```bash
dbs2go-json query datasets \
  --param dataset=/A/B/C \
  --param dataset=/D/E/F \
  --param detail=true \
  --output datasets.json \
  --ca-bundle "$X509_CERT_DIR"
```

## DBS instances

The predefined instances are:

```bash
--instance global
--instance phys01
--instance phys02
--instance phys03
```

A custom or local DBS Reader can be used with:

```bash
dbs2go-json query status \
  --base-url https://example.cern.ch/dbs2go \
  --proxy /tmp/x509up_u$(id -u) \
  --ca-bundle "$X509_CERT_DIR"
```

For a local unauthenticated test service:

```bash
dbs2go-json query status \
  --base-url http://127.0.0.1:8080/dbs2go \
  --no-cert \
  --ca-bundle "$X509_CERT_DIR"
```

## Environment variables

```text
DBS_INSTANCE       global, phys01, phys02, or phys03
DBS_READER_URL     complete DBS Reader base URL override
X509_USER_PROXY    combined proxy PEM path
X509_USER_CERT     certificate PEM path
X509_USER_KEY      private-key PEM path
```

## Tests

```bash
python -m pip install -e '.[dev]'
pytest
ruff check src tests
```

The tests use a local fake DBS HTTP server and do not require a CMS certificate.
