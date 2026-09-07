from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional, Union

from . import __version__
from .client import (
    DBS_INSTANCES,
    DBSClient,
    DBSClientError,
    CertificateConfig,
    discover_certificate,
)
from .output import dataset_output_dir, utc_now, write_json


def parse_key_value(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("parameters must use KEY=VALUE syntax")
    key, item = value.split("=", 1)
    key = key.strip()
    if not key:
        raise argparse.ArgumentTypeError("parameter key cannot be empty")
    return key, item


def params_dict(items: list[tuple[str, str]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in items:
        existing = output.get(key)
        if existing is None:
            output[key] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            output[key] = [existing, value]
    return output


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--instance",
        choices=sorted(DBS_INSTANCES),
        default=os.getenv("DBS_INSTANCE", "global"),
        help="DBS instance name (default: global)",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("DBS_READER_URL"),
        help="Override the complete DBS Reader base URL",
    )
    parser.add_argument("--proxy", help="Combined X.509 proxy PEM file")
    parser.add_argument("--cert", help="X.509 user certificate PEM file")
    parser.add_argument("--key", help="X.509 private-key PEM file")
    parser.add_argument(
        "--no-cert",
        action="store_true",
        help="Do not require a client certificate (useful for a local test server)",
    )
    parser.add_argument(
        "--ca-bundle",
        help="Custom CA bundle; by default the system trust store is used",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification (testing only)",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="HTTP timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="Retry count for transient errors")
    parser.add_argument("--compact", action="store_true", help="Write compact JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dbs2go-json",
        description="Query CMS DBS Reader/dbs2go and dump dataset information to JSON.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    add_common_options(common)

    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", parents=[common], help="Search for datasets")
    search.add_argument("pattern", help="DBS dataset pattern, e.g. /Muon/Run2024*/MINIAOD")
    search.add_argument("--access-type", default="*", help="VALID, PRODUCTION, INVALID, or *")
    search.add_argument(
        "--param",
        action="append",
        type=parse_key_value,
        default=[],
        metavar="KEY=VALUE",
        help="Additional datasets endpoint parameter; may be repeated",
    )
    search.add_argument("--output", type=Path, help="Write search response to this JSON file")
    search.add_argument("--names-only", action="store_true", help="Print only dataset names")
    search.set_defaults(handler=handle_search)

    dump = subparsers.add_parser("dump", parents=[common], help="Dump dataset metadata")
    dump.add_argument("pattern", help="Exact dataset name or wildcard pattern")
    dump.add_argument("--output", type=Path, default=Path("output"), help="Output directory")
    dump.add_argument("--access-type", default="*", help="VALID, PRODUCTION, INVALID, or *")
    dump.add_argument(
        "--param",
        action="append",
        type=parse_key_value,
        default=[],
        metavar="KEY=VALUE",
        help="Additional datasets search parameter; may be repeated",
    )
    dump.add_argument(
        "--include-files",
        action="store_true",
        help="Also dump full file metadata; this can be very large",
    )
    dump.add_argument(
        "--include-hierarchy",
        action="store_true",
        help="Recursively dump all parent and child datasets as hierarchy.json",
    )
    dump.add_argument(
        "--all-files",
        action="store_true",
        help="Include invalid files as well as valid files",
    )
    dump.add_argument(
        "--all",
        dest="include_all",
        action="store_true",
        help="Dump all optional data: file metadata, invalid files, and the complete hierarchy",
    )
    dump.add_argument(
        "--max-datasets",
        type=int,
        default=100,
        help="Safety limit for wildcard searches; 0 means unlimited",
    )
    dump.add_argument("--workers", type=int, default=4, help="Concurrent dataset workers")
    dump.add_argument(
        "--cache",
        type=Path,
        default=Path("cache.json"),
        help="Dataset-to-output directory cache JSON (default: ./cache.json)",
    )
    dump.set_defaults(handler=handle_dump)

    query = subparsers.add_parser("query", parents=[common], help="Run a read-only DBS endpoint")
    query.add_argument("endpoint", help="DBS Reader endpoint, e.g. datasets or filesummaries")
    query.add_argument(
        "--param",
        action="append",
        type=parse_key_value,
        default=[],
        metavar="KEY=VALUE",
        help="Endpoint parameter; may be repeated",
    )
    query.add_argument("--output", type=Path, help="Write response to this JSON file")
    query.set_defaults(handler=handle_query)

    return parser


def runtime_config(args: argparse.Namespace) -> tuple[str, CertificateConfig, Union[bool, str]]:
    base_url = args.base_url or DBS_INSTANCES[args.instance]
    certificate = discover_certificate(
        proxy=args.proxy,
        cert=args.cert,
        key=args.key,
        allow_no_certificate=args.no_cert,
    )
    verify: Union[bool, str]
    if args.insecure:
        verify = False
    elif args.ca_bundle:
        verify = args.ca_bundle
    else:
        verify = True
    return base_url, certificate, verify


def make_client(args: argparse.Namespace) -> DBSClient:
    base_url, certificate, verify = runtime_config(args)
    return DBSClient(
        base_url=base_url,
        certificate=certificate,
        verify=verify,
        timeout=args.timeout,
        retries=args.retries,
    )


def emit(payload: Any, *, output: Optional[Path], pretty: bool) -> None:
    if output:
        write_json(output, payload, pretty=pretty)
        print(output)
    else:
        json.dump(payload, sys.stdout, indent=2 if pretty else None, sort_keys=pretty)
        sys.stdout.write("\n")


def handle_search(args: argparse.Namespace) -> int:
    extra = params_dict(args.param)
    with make_client(args) as client:
        results = client.search_datasets(
            args.pattern,
            access_type=args.access_type,
            detail=not args.names_only,
            extra_params=extra,
        )

    if args.names_only:
        names = [
            row.get("dataset") for row in results if isinstance(row, dict) and row.get("dataset")
        ]
        if args.output:
            emit(names, output=args.output, pretty=not args.compact)
        else:
            for name in names:
                print(name)
        return 0

    emit(results, output=args.output, pretty=not args.compact)
    return 0


def dump_one_dataset(
    *,
    args: argparse.Namespace,
    dataset: str,
    root: Path,
    base_url: str,
    certificate: CertificateConfig,
    verify: Union[bool, str],
) -> dict[str, Any]:
    target = dataset_output_dir(root, dataset)
    try:
        with DBSClient(
            base_url=base_url,
            certificate=certificate,
            verify=verify,
            timeout=args.timeout,
            retries=args.retries,
        ) as client:
            sections = client.dataset_bundle(
                dataset,
                include_files=args.include_files,
                valid_files_only=not args.all_files,
            )
            if args.include_hierarchy:
                sections["hierarchy"] = client.dataset_hierarchy(dataset)

        fetched_at = utc_now()
        bundle = {
            "schema_version": 1,
            "dataset": dataset,
            "source": {
                "base_url": base_url,
                "instance": args.instance,
                "fetched_at": fetched_at,
            },
            "configuration": {
                "output_configs": sections["output_configs"],
                "global_tags": sorted(
                    {
                        row["global_tag"]
                        for row in sections["output_configs"]
                        if isinstance(row, dict) and isinstance(row.get("global_tag"), str)
                    }
                ),
            },
            "sections": sections,
        }
        write_json(target / "bundle.json", bundle, pretty=not args.compact)
        write_json(target / "configuration.json", bundle["configuration"], pretty=not args.compact)
        for section, payload in sections.items():
            write_json(target / f"{section}.json", payload, pretty=not args.compact)
        return {
            "dataset": dataset,
            "status": "ok",
            "path": str(target),
            "fetched_at": fetched_at,
        }
    except Exception as exc:  # failure is recorded in the manifest
        return {
            "dataset": dataset,
            "status": "error",
            "path": str(target),
            "error": str(exc),
        }


def update_cache(cache_path: Path, results: list[dict[str, Any]], *, pretty: bool) -> None:
    """Update the persistent dataset-to-output-directory index after a dump."""
    cache: dict[str, Any] = {"schema_version": 1, "datasets": {}}
    if cache_path.exists():
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("datasets"), dict):
                cache = payload
        except (OSError, json.JSONDecodeError):
            pass

    datasets = cache.setdefault("datasets", {})
    for item in results:
        if item["status"] == "ok":
            datasets[item["dataset"]] = {
                "path": item["path"],
                "fetched_at": item["fetched_at"],
            }
    cache["schema_version"] = 1
    cache["updated_at"] = utc_now()
    write_json(cache_path, cache, pretty=pretty)


def handle_dump(args: argparse.Namespace) -> int:
    if args.include_all:
        args.include_files = True
        args.include_hierarchy = True
        args.all_files = True

    if args.workers < 1:
        raise DBSClientError("--workers must be at least 1")
    if args.max_datasets < 0:
        raise DBSClientError("--max-datasets cannot be negative")

    root = args.output.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    base_url, certificate, verify = runtime_config(args)

    extra = params_dict(args.param)
    with DBSClient(
        base_url=base_url,
        certificate=certificate,
        verify=verify,
        timeout=args.timeout,
        retries=args.retries,
    ) as client:
        matches = client.search_datasets(
            args.pattern,
            access_type=args.access_type,
            detail=True,
            extra_params=extra,
        )

    datasets = sorted(
        {
            row["dataset"]
            for row in matches
            if isinstance(row, dict) and isinstance(row.get("dataset"), str)
        }
    )
    if args.max_datasets and len(datasets) > args.max_datasets:
        raise DBSClientError(
            f"Pattern matched {len(datasets)} datasets, exceeding --max-datasets={args.max_datasets}. "
            "Use a narrower pattern or explicitly raise the limit."
        )

    write_json(
        root / "search_results.json",
        {
            "schema_version": 1,
            "pattern": args.pattern,
            "access_type": args.access_type,
            "base_url": base_url,
            "fetched_at": utc_now(),
            "results": matches,
        },
        pretty=not args.compact,
    )

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(args.workers, max(1, len(datasets)))) as executor:
        futures = {
            executor.submit(
                dump_one_dataset,
                args=args,
                dataset=dataset,
                root=root,
                base_url=base_url,
                certificate=certificate,
                verify=verify,
            ): dataset
            for dataset in datasets
        }
        for future in as_completed(futures):
            item = future.result()
            results.append(item)
            if item["status"] == "ok":
                print(f"[ok] {item['dataset']}", file=sys.stderr)
            else:
                print(f"[error] {item['dataset']}: {item['error']}", file=sys.stderr)

    results.sort(key=lambda item: item["dataset"])
    failures = [item for item in results if item["status"] == "error"]
    manifest = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "query": {
            "pattern": args.pattern,
            "access_type": args.access_type,
            "extra_params": extra,
            "include_all": args.include_all,
            "include_files": args.include_files,
            "include_hierarchy": args.include_hierarchy,
            "valid_files_only": not args.all_files,
        },
        "source": {
            "instance": args.instance,
            "base_url": base_url,
            "credential": certificate.description,
        },
        "counts": {
            "matched": len(datasets),
            "successful": len(results) - len(failures),
            "failed": len(failures),
        },
        "datasets": results,
    }
    write_json(root / "manifest.json", manifest, pretty=not args.compact)
    update_cache(args.cache.expanduser().resolve(), results, pretty=not args.compact)
    print(root / "manifest.json")
    return 1 if failures else 0


def handle_query(args: argparse.Namespace) -> int:
    with make_client(args) as client:
        payload = client.get(args.endpoint, params_dict(args.param))
    emit(payload, output=args.output, pretty=not args.compact)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except DBSClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
