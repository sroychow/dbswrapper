from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from dbs2go_wrapper.client import (
    DBSClient,
    DBSClientError,
    CertificateConfig,
    discover_certificate,
)


def test_discover_explicit_proxy(tmp_path: Path) -> None:
    proxy = tmp_path / "proxy.pem"
    proxy.write_text("dummy", encoding="utf-8")

    config = discover_certificate(proxy=str(proxy))

    assert config.request_value == str(proxy)
    assert config.description == "x509-proxy"


def test_discover_missing_explicit_proxy_fails(tmp_path: Path) -> None:
    with pytest.raises(DBSClientError):
        discover_certificate(proxy=str(tmp_path / "missing.pem"))


def test_client_search_builds_expected_request() -> None:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = [{"dataset": "/A/B/C"}]
    response.url = "https://example.invalid/datasets?dataset=%2FA%2F%2A%2FC"

    session = Mock()
    session.get.return_value = response
    client = DBSClient(
        base_url="https://example.invalid",
        certificate=CertificateConfig(None, "none"),
        session=session,
    )

    result = client.search_datasets("/A/*/C", access_type="*", detail=True)

    assert result == [{"dataset": "/A/B/C"}]
    _, kwargs = session.get.call_args
    assert kwargs["params"]["dataset"] == "/A/*/C"
    assert kwargs["params"]["dataset_access_type"] == "*"
    assert kwargs["params"]["detail"] == "true"


def test_client_rejects_writer_endpoint() -> None:
    client = DBSClient(
        base_url="https://example.invalid",
        certificate=CertificateConfig(None, "none"),
        session=Mock(),
    )

    with pytest.raises(DBSClientError):
        client.get("bulkblocks")
