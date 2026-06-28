"""URLParser — validates and parses URLs into structured components."""

from __future__ import annotations


class URLParser:
    """Parses a URL string and validates its components.

    Returns a dictionary with parsed fields and validation results.
    Critical errors (missing/invalid protocol, invalid TLD) make valid=False.
    Warnings (IP address as domain) go into errors[] but keep valid=True.
    """

    VALID_PROTOCOLS: set[str] = {"http", "https"}
    VALID_TLDS: set[str] = {"com", "org", "net", "io", "dev"}
    DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}

    @staticmethod
    def _is_ip_address(domain: str) -> bool:
        """Check if domain string is a valid IPv4 address."""
        parts = domain.split(".")
        if len(parts) != 4:
            return False
        try:
            return all(0 <= int(part) <= 255 for part in parts)
        except ValueError:
            return False

    @staticmethod
    def parse(url: str) -> dict:
        """Parse a URL into its components and validate.

        Args:
            url: The URL string to parse.

        Returns:
            dict with keys: valid, protocol, domain, port, path, query,
            fragment, errors.
        """
        critical_errors: list[str] = []
        warnings: list[str] = []

        # ── Edge case: non-string input ────────────────────────────────────
        if not isinstance(url, str):
            raise TypeError(f"URL must be a string, got {type(url).__name__}")

        # ── Edge case: empty string ──────────────────────────────────────
        if not url:
            return {
                "valid": False,
                "protocol": "",
                "domain": "",
                "port": 0,
                "path": "",
                "query": {},
                "fragment": "",
                "errors": ["Empty URL"],
            }

        protocol = ""
        domain = ""
        port = 0
        path = ""
        query: dict[str, str] = {}
        fragment = ""

        rest = url

        # ── Extract fragment ────────────────────────────────────────────
        if "#" in rest:
            rest, fragment = rest.split("#", 1)

        # ── Extract protocol ────────────────────────────────────────────
        if "://" in rest:
            protocol, rest = rest.split("://", 1)
            if protocol not in URLParser.VALID_PROTOCOLS:
                critical_errors.append(f"Invalid protocol: {protocol}")
        else:
            critical_errors.append("Missing protocol")

        # ── Extract query string ────────────────────────────────────────
        if "?" in rest:
            rest, query_str = rest.split("?", 1)
            for pair in query_str.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    query[k] = v
                elif pair:
                    query[pair] = ""

        # ── Extract path ────────────────────────────────────────────────
        if "/" in rest:
            domain_part, path_part = rest.split("/", 1)
            path = "/" + path_part
        else:
            domain_part = rest

        # ── Extract port from domain ────────────────────────────────────
        if ":" in domain_part:
            domain, port_str = domain_part.rsplit(":", 1)
            try:
                port = int(port_str)
            except ValueError:
                critical_errors.append(f"Invalid port: {port_str}")
        else:
            domain = domain_part
            # Apply default port based on valid protocol
            if protocol in URLParser.DEFAULT_PORTS:
                port = URLParser.DEFAULT_PORTS[protocol]

        # ── Validate domain ─────────────────────────────────────────────
        if not domain:
            critical_errors.append("Missing domain")
        elif URLParser._is_ip_address(domain):
            warnings.append("IP address as domain")
        else:
            parts = domain.split(".")
            if len(parts) < 2:
                critical_errors.append(f"Invalid domain: {domain}")
            else:
                tld = parts[-1]
                if tld not in URLParser.VALID_TLDS:
                    critical_errors.append(f"Invalid TLD: {tld}")

        # ── Determine overall validity ──────────────────────────────────
        valid = len(critical_errors) == 0
        errors = critical_errors + warnings

        return {
            "valid": valid,
            "protocol": protocol,
            "domain": domain,
            "port": port,
            "path": path,
            "query": query,
            "fragment": fragment,
            "errors": errors,
        }
