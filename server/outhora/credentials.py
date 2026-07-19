"""Temporary credential management for Outhora."""

from __future__ import annotations

import os
import tempfile
from typing import Any

from server.outhora.models import CredentialResponse


def inject_aws_credentials(creds: CredentialResponse, env: dict[str, str]) -> dict[str, str]:
    """Inject AWS temporary credentials into an environment dict."""
    env["AWS_ACCESS_KEY_ID"] = creds.access_key
    env["AWS_SECRET_ACCESS_KEY"] = creds.secret_key
    if creds.session_token:
        env["AWS_SESSION_TOKEN"] = creds.session_token
    # Remove any static credential config
    env.pop("AWS_SHARED_CREDENTIALS_FILE", None)
    env.pop("AWS_CONFIG_FILE", None)
    return env


def inject_gh_credentials(creds: CredentialResponse, env: dict[str, str]) -> dict[str, str]:
    """Inject GitHub token into environment."""
    token = creds.extra.get("gh_token", creds.access_key)
    env["GH_TOKEN"] = token
    env["GITHUB_TOKEN"] = token
    return env


def inject_kubectl_credentials(creds: CredentialResponse, env: dict[str, str]) -> dict[str, str]:
    """Inject Kubernetes credentials into environment.

    kubectl only reads credentials from a kubeconfig file (KUBECONFIG) or
    command-line flags — there is no token env var. So the temporary
    kubeconfig is written to a private temp file for this one execution.
    """
    if creds.extra.get("kubeconfig_data"):
        fd, path = tempfile.mkstemp(prefix="kubeconfig-", suffix=".yaml")
        with os.fdopen(fd, "w") as f:  # mkstemp creates the file mode 0600
            f.write(creds.extra["kubeconfig_data"])
        env["KUBECONFIG"] = path
    return env


def inject_terraform_credentials(creds: CredentialResponse, env: dict[str, str]) -> dict[str, str]:
    """Inject Terraform credentials (typically cloud provider creds)."""
    # Terraform uses the same AWS/GCP/Azure env vars
    env = inject_aws_credentials(creds, env)
    if creds.extra.get("tf_token"):
        env["TF_TOKEN_app_terraform_io"] = creds.extra["tf_token"]
    return env


def inject_psql_credentials(creds: CredentialResponse, env: dict[str, str]) -> dict[str, str]:
    """Inject PostgreSQL credentials into environment."""
    if creds.extra.get("pgpassword"):
        env["PGPASSWORD"] = creds.extra["pgpassword"]
    if creds.extra.get("pguser"):
        env["PGUSER"] = creds.extra["pguser"]
    if creds.extra.get("pghost"):
        env["PGHOST"] = creds.extra["pghost"]
    if creds.extra.get("pgdatabase"):
        env["PGDATABASE"] = creds.extra["pgdatabase"]
    return env


def inject_az_credentials(creds: CredentialResponse, env: dict[str, str]) -> dict[str, str]:
    """Inject Azure temporary credentials into environment."""
    if creds.extra.get("client_id"):
        env["AZURE_CLIENT_ID"] = creds.extra["client_id"]
    if creds.extra.get("client_secret"):
        env["AZURE_CLIENT_SECRET"] = creds.extra["client_secret"]
    if creds.extra.get("tenant_id"):
        env["AZURE_TENANT_ID"] = creds.extra["tenant_id"]
    if creds.extra.get("subscription_id"):
        env["AZURE_SUBSCRIPTION_ID"] = creds.extra["subscription_id"]
    return env


CREDENTIAL_INJECTORS = {
    "aws": inject_aws_credentials,
    "az": inject_az_credentials,
    "gh": inject_gh_credentials,
    "kubectl": inject_kubectl_credentials,
    "terraform": inject_terraform_credentials,
    "psql": inject_psql_credentials,
}


def build_execution_env(tool: str, creds: CredentialResponse) -> dict[str, str]:
    """Build a clean environment with injected temporary credentials.

    Starts from the current environment but strips known static credential
    paths, then injects Outhora-issued temporary credentials.
    """
    env = dict(os.environ)

    # Strip static credential paths
    for key in [
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_CONFIG_FILE",
        "KUBECONFIG",
        "PGPASSFILE",
    ]:
        env.pop(key, None)

    injector = CREDENTIAL_INJECTORS.get(tool)
    if injector:
        env = injector(creds, env)

    return env
