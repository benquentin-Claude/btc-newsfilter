"""Anmeldung bei der Claude-API.

In GitHub Actions ohne festen Schlüssel: GitHub stellt dem Lauf einen kurzlebigen Ausweis aus (OIDC), Anthropic
tauscht ihn gegen einen Zugang für wenige Minuten (Workload Identity Federation, eingerichtet in der Claude Console).
Dafür braucht der Workflow `id-token: write` und die Variablen ANTHROPIC_FEDERATION_RULE_ID,
ANTHROPIC_ORGANIZATION_ID und ANTHROPIC_SERVICE_ACCOUNT_ID (keine Geheimnisse). Ohne diese Variablen, etwa am Mac,
gilt wie bisher ANTHROPIC_API_KEY.
"""
import json
import os
import urllib.request

AUDIENCE = "https://api.anthropic.com"


def foederation_aktiv():
    return bool(os.environ.get("ANTHROPIC_FEDERATION_RULE_ID") and os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"))


def github_ausweis():
    """Holt bei jedem Aufruf einen frischen GitHub-Ausweis (jeder darf nur einmal getauscht werden)."""
    req = urllib.request.Request(f"{os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']}&audience={AUDIENCE}",
                                 headers={"Authorization": f"Bearer {os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["value"]


def client():
    import anthropic

    if not foederation_aktiv():
        return anthropic.Anthropic()
    return anthropic.Anthropic(credentials=anthropic.WorkloadIdentityCredentials(
        identity_token_provider=github_ausweis,
        federation_rule_id=os.environ["ANTHROPIC_FEDERATION_RULE_ID"],
        organization_id=os.environ["ANTHROPIC_ORGANIZATION_ID"],
        service_account_id=os.environ.get("ANTHROPIC_SERVICE_ACCOUNT_ID") or None,
        workspace_id=os.environ.get("ANTHROPIC_WORKSPACE_ID") or None,
    ))
