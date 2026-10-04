"""Verify WorkOS Connect access JWTs using the configured issuer's public JWKS."""

import asyncio

import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier


class WorkOSTokenVerifier(TokenVerifier):
    def __init__(self, issuer_url: str, resource_server_url: str) -> None:
        self.issuer_url = issuer_url
        self.resource_server_url = resource_server_url
        # Cache public key sets for five minutes; keep PyJWT's bounded unknown-kid
        # refresh/cooldown behavior, without indefinitely caching individual keys.
        self.jwks_client = jwt.PyJWKClient(
            issuer_url.rstrip("/") + "/oauth2/jwks",
            cache_keys=False, cache_jwk_set=True, lifespan=300, timeout=10,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        # PyJWKClient does synchronous HTTPS I/O. Signature work also stays off-loop.
        return await asyncio.to_thread(self._verify_token, token)

    def _verify_token(self, token: str) -> AccessToken | None:
        try:
            header = jwt.get_unverified_header(token)
            # WorkOS Connect uses RS256. Never derive the allowlist or JWKS URL
            # from untrusted alg/jku/x5u header values.
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
                return None
            key = self.jwks_client.get_signing_key(header["kid"])
            claims = jwt.decode(
                token, key, algorithms=["RS256"], issuer=self.issuer_url,
                audience=self.resource_server_url,
                options={"require": ["iss", "aud", "exp", "iat", "sub", "client_id"]},
            )
            if any(not isinstance(claims[name], str) or not claims[name].strip()
                   for name in ("sub", "client_id")):
                return None
            if any(type(claims[name]) is not int for name in ("exp", "iat")):
                return None
            scope = claims.get("scope", "")
            if not isinstance(scope, str):
                return None
            return AccessToken(
                token=token, client_id=claims["client_id"], scopes=scope.split(),
                expires_at=claims["exp"], resource=self.resource_server_url,
                subject=claims["sub"], claims={"iss": self.issuer_url},
            )
        except (jwt.PyJWTError, ValueError, TypeError, OSError):
            # Invalid JWTs and unavailable/malformed JWKS fail closed. Never log
            # token data or exception text; the SDK produces the standard 401.
            return None
