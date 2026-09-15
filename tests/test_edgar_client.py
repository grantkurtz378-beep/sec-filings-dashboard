"""Tests for the retry/backoff wrapper around SEC's API. `requests.get` and `time.sleep`
are both mocked so these run instantly and never touch the network."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.edgar_client import MAX_RETRIES, _rate_limited_get


def _response(status_code: int, headers: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(f"{status_code} error")
    return resp


@patch("time.sleep", return_value=None)
@patch("requests.get")
def test_succeeds_immediately_on_first_try(mock_get, _mock_sleep):
    mock_get.return_value = _response(200)
    resp = _rate_limited_get("https://example.com")
    assert resp.status_code == 200
    assert mock_get.call_count == 1


@patch("time.sleep", return_value=None)
@patch("requests.get")
def test_retries_on_429_then_succeeds(mock_get, _mock_sleep):
    mock_get.side_effect = [_response(429), _response(200)]
    resp = _rate_limited_get("https://example.com")
    assert resp.status_code == 200
    assert mock_get.call_count == 2


@patch("time.sleep", return_value=None)
@patch("requests.get")
def test_retries_on_5xx_then_succeeds(mock_get, _mock_sleep):
    mock_get.side_effect = [_response(503), _response(200)]
    resp = _rate_limited_get("https://example.com")
    assert resp.status_code == 200


@patch("time.sleep", return_value=None)
@patch("requests.get")
def test_gives_up_after_max_retries(mock_get, _mock_sleep):
    mock_get.return_value = _response(503)
    with pytest.raises(requests.exceptions.HTTPError):
        _rate_limited_get("https://example.com")
    assert mock_get.call_count == MAX_RETRIES


@patch("time.sleep", return_value=None)
@patch("requests.get")
def test_does_not_retry_a_plain_404(mock_get, _mock_sleep):
    """A 404 means the resource genuinely doesn't exist - retrying wastes 3x the time
    for the same outcome and looks like slow network rather than a clear "not found"."""
    mock_get.return_value = _response(404)
    with pytest.raises(requests.exceptions.HTTPError):
        _rate_limited_get("https://example.com")
    assert mock_get.call_count == 1


@patch("time.sleep", return_value=None)
@patch("requests.get")
def test_retries_on_connection_error(mock_get, _mock_sleep):
    mock_get.side_effect = [requests.exceptions.ConnectionError("boom"), _response(200)]
    resp = _rate_limited_get("https://example.com")
    assert resp.status_code == 200


@patch("time.sleep", return_value=None)
@patch("requests.get")
def test_respects_retry_after_header(mock_get, mock_sleep):
    mock_get.side_effect = [_response(429, headers={"Retry-After": "7"}), _response(200)]
    _rate_limited_get("https://example.com")
    # one of the sleep calls should honor the server's requested delay
    assert 7.0 in [call.args[0] for call in mock_sleep.call_args_list]
