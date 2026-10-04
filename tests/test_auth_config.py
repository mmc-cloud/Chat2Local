"""Optional OAuth configuration and safe startup validation."""

import pytest

from chat2local.runtime.config import AppConfig, AuthConfig, ConfigError, load_config
from test_config import write_config

OAUTH = {
    "mode": "oauth", "provider": "workos", "issuer_url": "https://test.authkit.app",
    "resource_server_url": "https://example.com/mcp",
}


@pytest.mark.parametrize("text", ["", "{}", "read:\n  max_lines: 10\n", "auth:\n  mode: none\n"])
def test_old_and_none_configs_need_no_oauth_parameters(tmp_path, text):
    assert load_config(write_config(tmp_path, text)).auth == AuthConfig()
    assert AppConfig().auth.mode == "none"


def test_workos_config_loads_and_preserves_exact_urls(tmp_path):
    import yaml
    path = write_config(tmp_path, yaml.safe_dump({"auth": OAUTH}))
    assert load_config(path).auth.model_dump() == OAUTH
    assert AuthConfig(**(OAUTH | {"issuer_url": "https://test.authkit.app/"})).issuer_url.endswith("/")


@pytest.mark.parametrize("url", ["https://example.com/mcp", "https://chat2local.example.com/mcp"])
def test_resource_url_accepts_fixed_mcp_path(tmp_path, url):
    import yaml
    path = write_config(tmp_path, yaml.safe_dump({"auth": OAUTH | {"resource_server_url": url}}))
    assert load_config(path).auth.resource_server_url == url


@pytest.mark.parametrize("path", ["", "/", "/external/mcp", "/mcp/", "/MCP", "/%6dcp", "/mcp/../mcp"])
def test_resource_url_rejects_other_paths(tmp_path, path):
    import yaml
    data = {"auth": OAUTH | {"resource_server_url": "https://example.com" + path}}
    with pytest.raises(ConfigError, match=r"auth.resource_server_url: Value error, path must be exactly /mcp"):
        load_config(write_config(tmp_path, yaml.safe_dump(data)))


@pytest.mark.parametrize("field", ["provider", "issuer_url", "resource_server_url"])
def test_oauth_requires_each_parameter(tmp_path, field):
    import yaml
    data = {key: value for key, value in OAUTH.items() if key != field}
    with pytest.raises(ConfigError, match=f"auth.{field} is required"):
        load_config(write_config(tmp_path, yaml.safe_dump({"auth": data})))


@pytest.mark.parametrize("field,value", [
    ("mode", "unknown"), ("provider", "auth0"), ("issuer_url", 123),
    ("resource_server_url", True), ("issuerr_url", "https://typo.example.com"),
    ("allowed_subjects", ["user"]), ("required_scopes", ["admin"]),
])
def test_bad_auth_fields_fail(tmp_path, field, value):
    import yaml
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, yaml.safe_dump({"auth": OAUTH | {field: value}})))


@pytest.mark.parametrize("field", ["issuer_url", "resource_server_url"])
@pytest.mark.parametrize("url", [
    "http://host", "ftp://host", "https://", "", "https://host:65536", "https://host:0",
    "https://user:PRIVATE-SECRET@host", "https://host?q=PRIVATE-SECRET", "https://host#secret",
    " https://host", "https://host\n", "https://host\\path", "https://[broken",
])
def test_oauth_urls_rejected_without_echoing_values(tmp_path, field, url):
    import yaml
    with pytest.raises(ConfigError) as failure:
        load_config(write_config(tmp_path, yaml.safe_dump({"auth": OAUTH | {field: url}})))
    assert f"auth.{field}" in str(failure.value)
    assert "PRIVATE-SECRET" not in str(failure.value)
