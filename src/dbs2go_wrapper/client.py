from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DBS_INSTANCES: dict[str, str] = {
    "global": "https://cmsweb.cern.ch/dbs/prod/global/DBSReader",
    "phys01": "https://cmsweb.cern.ch/dbs/prod/phys01/DBSReader",
    "phys02": "https://cmsweb.cern.ch/dbs/prod/phys02/DBSReader",
    "phys03": "https://cmsweb.cern.ch/dbs/prod/phys03/DBSReader",
}

READ_ENDPOINTS = {
    "datasets",
    "filesummaries",
    "runs",
    "runsummaries",
    "blocks",
    "blocksummaries",
    "blocklocations",
    "files",
    "outputconfigs",
    "datasetparents",
    "datasetchildren",
    "blockorigin",
    "filelumis",
    "fileparents",
    "filechildren",
    "datatiers",
    "primarydatasets",
    "acquisitioneras",
    "releaseversions",
    "physicsgroups",
    "datasetaccesstypes",
    "serverinfo",
    "status",
    "apis",
}


class DBSClientError(RuntimeError):
    """Raised when a DBS request cannot be completed or parsed."""


@dataclass(frozen=True)
class CertificateConfig:
    """Resolved client-certificate configuration for requests."""

    request_value: Optional[Union[str, tuple[str, str]]]
    description: str


def _existing(path: Optional[Union[str, os.PathLike[str]]]) -> Optional[Path]:
    if not path:
        return None
    candidate = Path(path).expanduser()
    return candidate if candidate.is_file() else None


def _require_unencrypted_private_key(path: Path) -> None:
    """Reject keys that Requests cannot use without an interactive password."""
    try:
        contents = path.read_bytes()
    except OSError:
        return
    if b"ENCRYPTED PRIVATE KEY" in contents or b"Proc-Type: 4,ENCRYPTED" in contents:
        raise DBSClientError(
            f"Private key is encrypted: {path}. Requests cannot prompt for a key password. "
            "Create a CMS proxy with 'voms-proxy-init -voms cms' and use --proxy "
            "or X509_USER_PROXY, or provide an unencrypted key in a protected file."
        )


def _certificate_key_config(cert_path: Path, key_path: Path) -> CertificateConfig:
    _require_unencrypted_private_key(key_path)
    return CertificateConfig(
        (str(cert_path), str(key_path)),
        "x509-certificate-key",
    )


def discover_certificate(
    *,
    proxy: Optional[str] = None,
    cert: Optional[str] = None,
    key: Optional[str] = None,
    allow_no_certificate: bool = False,
) -> CertificateConfig:
    """Resolve an X.509 proxy or certificate/key pair.

    Resolution order:
    1. Explicit ``proxy`` argument.
    2. Explicit ``cert`` and ``key`` arguments.
    3. ``X509_USER_PROXY``.
    4. ``/tmp/x509up_u<uid>``.
    5. ``X509_USER_CERT`` and ``X509_USER_KEY``.
    6. ``~/.globus/usercert.pem`` and ``~/.globus/userkey.pem``.
    """

    explicit_proxy = _existing(proxy)
    if proxy and not explicit_proxy:
        raise DBSClientError(f"X.509 proxy does not exist: {Path(proxy).expanduser()}")
    if explicit_proxy:
        return CertificateConfig(str(explicit_proxy), "x509-proxy")

    if cert or key:
        cert_path = _existing(cert)
        key_path = _existing(key)
        if not cert_path or not key_path:
            raise DBSClientError("Both --cert and --key must point to existing files")
        return _certificate_key_config(cert_path, key_path)

    env_proxy = _existing(os.getenv("X509_USER_PROXY"))
    if env_proxy:
        return CertificateConfig(str(env_proxy), "x509-proxy")

    try:
        default_proxy = _existing(f"/tmp/x509up_u{os.getuid()}")
    except AttributeError:  # pragma: no cover - non-POSIX fallback
        default_proxy = None
    if default_proxy:
        return CertificateConfig(str(default_proxy), "x509-proxy")

    env_cert = _existing(os.getenv("X509_USER_CERT"))
    env_key = _existing(os.getenv("X509_USER_KEY"))
    if env_cert and env_key:
        return _certificate_key_config(env_cert, env_key)

    globus_cert = _existing("~/.globus/usercert.pem")
    globus_key = _existing("~/.globus/userkey.pem")
    if globus_cert and globus_key:
        return _certificate_key_config(globus_cert, globus_key)

    if allow_no_certificate:
        return CertificateConfig(None, "none")

    raise DBSClientError(
        "No X.509 credential found. Create a CMS proxy, set X509_USER_PROXY, "
        "or pass --proxy / --cert and --key."
    )


def make_session(retries: int = 3, user_agent: str = "dbs2go-wrapper/0.1.0") -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": user_agent,
        }
    )
    return session


class DBSClient:
    """Small read-only client for DBS Reader/dbs2go HTTP APIs."""

    def __init__(
        self,
        *,
        base_url: str,
        certificate: CertificateConfig,
        verify: Union[bool, str] = True,
        timeout: float = 60.0,
        retries: int = 3,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.certificate = certificate
        self.verify = verify
        self.timeout = timeout
        self.session = session or make_session(retries=retries)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "DBSClient":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def get(
        self,
        endpoint: str,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Union[list[dict[str, Any]], dict[str, Any]]:
        endpoint = endpoint.strip("/")
        if endpoint not in READ_ENDPOINTS:
            raise DBSClientError(
                f"Unsupported or non-read endpoint '{endpoint}'. "
                f"Allowed endpoints: {', '.join(sorted(READ_ENDPOINTS))}"
            )

        url = f"{self.base_url}/{endpoint}"
        try:
            response = self.session.get(
                url,
                params=dict(params or {}),
                cert=self.certificate.request_value,
                verify=self.verify,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            message = self._request_error_message(exc)
            raise DBSClientError(message) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            preview = response.text[:500].replace("\n", " ")
            raise DBSClientError(
                f"DBS returned a non-JSON response from {response.url}: {preview}"
            ) from exc

        if not isinstance(payload, (list, dict)):
            raise DBSClientError(
                f"Unexpected DBS response type {type(payload).__name__} from {response.url}"
            )
        return payload

    @staticmethod
    def _request_error_message(exc: requests.RequestException) -> str:
        response = exc.response
        if response is None:
            return f"DBS request failed: {exc}"

        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, list) and payload and isinstance(payload[0], dict):
                detail = str(payload[0].get("message") or payload[0].get("error") or "")
            elif isinstance(payload, dict):
                detail = str(payload.get("message") or payload.get("error") or "")
        except ValueError:
            detail = response.text[:500].replace("\n", " ")

        suffix = f": {detail}" if detail else ""
        return f"DBS request failed with HTTP {response.status_code} for {response.url}{suffix}"

    def search_datasets(
        self,
        pattern: str,
        *,
        access_type: str = "*",
        detail: bool = True,
        extra_params: Optional[Mapping[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "dataset": pattern,
            "dataset_access_type": access_type,
            "detail": str(detail).lower(),
        }
        params.update(extra_params or {})
        payload = self.get("datasets", params)
        if not isinstance(payload, list):
            raise DBSClientError("The datasets endpoint returned an object instead of a list")
        return payload

    def dataset_bundle(
        self,
        dataset: str,
        *,
        include_files: bool = False,
        valid_files_only: bool = True,
    ) -> dict[str, Any]:
        sections: dict[str, tuple[str, dict[str, Any]]] = {
            "metadata": (
                "datasets",
                {
                    "dataset": dataset,
                    "detail": "true",
                    "dataset_access_type": "*",
                },
            ),
            "summary": (
                "filesummaries",
                {
                    "dataset": dataset,
                    **({"validFileOnly": "1"} if valid_files_only else {}),
                },
            ),
            "runs": ("runs", {"dataset": dataset}),
            "blocks": ("blocks", {"dataset": dataset, "detail": "true"}),
            "output_configs": ("outputconfigs", {"dataset": dataset}),
            "parents": ("datasetparents", {"dataset": dataset}),
            "children": ("datasetchildren", {"dataset": dataset}),
        }
        if include_files:
            sections["files"] = (
                "files",
                {
                    "dataset": dataset,
                    "detail": "true",
                    **({"validFileOnly": "1"} if valid_files_only else {}),
                },
            )

        result: dict[str, Any] = {}
        for name, (endpoint, params) in sections.items():
            result[name] = self.get(endpoint, params)
        result["site_replicas"] = self.dataset_site_replicas(dataset, result["blocks"])
        return result

    def dataset_site_replicas(
        self, dataset: str, blocks: Union[list[dict[str, Any]], dict[str, Any]]
    ) -> dict[str, Any]:
        """Collect block replica locations and dataset-level site replication metrics."""
        if not isinstance(blocks, list):
            raise DBSClientError("The blocks endpoint returned an object instead of a list")

        locations_by_block: list[dict[str, Any]] = []
        site_blocks: dict[str, list[dict[str, Any]]] = {}
        replicated_blocks = 0
        replicated_files = 0
        total_files = 0

        for block in blocks:
            if not isinstance(block, dict) or not isinstance(block.get("block_name"), str):
                continue
            block_name = block["block_name"]
            file_count = block.get("file_count", 0)
            if not isinstance(file_count, int):
                file_count = 0
            total_files += file_count
            try:
                payload = self.get("blocklocations", {"block_name": block_name})
            except DBSClientError as exc:
                if "HTTP 404" in str(exc):
                    return {
                        "available": False,
                        "source": "DBSReader/blocklocations",
                        "reason": (
                            "The configured DBS Reader does not provide the blocklocations "
                            "endpoint, so site replica metrics could not be collected."
                        ),
                    }
                raise
            if not isinstance(payload, list):
                raise DBSClientError(
                    "The blocklocations endpoint returned an object instead of a list"
                )
            locations = sorted(
                {
                    str(row.get("phedex_node_name") or row.get("site_name") or row.get("location"))
                    for row in payload
                    if isinstance(row, dict)
                    and (row.get("phedex_node_name") or row.get("site_name") or row.get("location"))
                }
            )
            locations_by_block.append({"block_name": block_name, "locations": locations})
            if len(locations) > 1:
                replicated_blocks += 1
                replicated_files += file_count
            for site in locations:
                site_blocks.setdefault(site, []).append(
                    {"block_name": block_name, "file_count": file_count}
                )

        block_count = len(locations_by_block)
        sites = [
            {
                "site": site,
                "block_count": len(site_entries),
                "file_count": sum(entry["file_count"] for entry in site_entries),
                "fraction_of_blocks": len(site_entries) / block_count if block_count else 0.0,
                "fraction_of_files": (
                    sum(entry["file_count"] for entry in site_entries) / total_files
                    if total_files
                    else 0.0
                ),
            }
            for site, site_entries in sorted(site_blocks.items())
        ]
        return {
            "dataset": dataset,
            "block_locations": locations_by_block,
            "sites": sites,
            "replication": {
                "site_count": len(sites),
                "block_count": block_count,
                "replicated_block_count": replicated_blocks,
                "fraction_of_blocks_replicated": replicated_blocks / block_count
                if block_count
                else 0.0,
                "file_count": total_files,
                "replicated_file_count": replicated_files,
                "fraction_of_files_replicated": replicated_files / total_files
                if total_files
                else 0.0,
            },
        }

    def dataset_hierarchy(self, dataset: str) -> dict[str, Any]:
        """Return every ancestor and descendant of ``dataset`` as a tree.

        DBS returns only directly related datasets from the parent and child
        endpoints.  This method follows each direction independently so the
        returned hierarchy does not loop back through the dataset being dumped.
        """

        def related_datasets(endpoint: str, current: str, field: str) -> list[str]:
            payload = self.get(endpoint, {"dataset": current})
            if not isinstance(payload, list):
                raise DBSClientError(
                    f"The {endpoint} endpoint returned an object instead of a list"
                )
            return sorted(
                {
                    row[field]
                    for row in payload
                    if isinstance(row, dict) and isinstance(row.get(field), str)
                }
            )

        def walk(
            endpoint: str, field: str, branch: str, current: str, seen: set[str]
        ) -> list[dict[str, Any]]:
            nodes: list[dict[str, Any]] = []
            for related in related_datasets(endpoint, current, field):
                node: dict[str, Any] = {"dataset": related}
                if related in seen:
                    node["cycle"] = True
                else:
                    node[branch] = walk(endpoint, field, branch, related, seen | {related})
                nodes.append(node)
            return nodes

        return {
            "dataset": dataset,
            "parents": walk("datasetparents", "parent_dataset", "parents", dataset, {dataset}),
            "children": walk("datasetchildren", "child_dataset", "children", dataset, {dataset}),
        }
