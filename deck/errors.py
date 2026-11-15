"""Exceptions shared by the store, CLI and HTTP API (frozen contract, design §5.2, §11).

Each error carries the CLI exit code and the HTTP error code it maps to.
"""


class DeckError(Exception):
    exit_code = 1
    code = "error"

    def __init__(self, reason="", **data):
        super().__init__(reason)
        self.reason = reason
        self.data = data


class ValidationError(DeckError):
    """Bad input: schema violation, brief validator error (exit 2)."""
    exit_code = 2
    code = "invalid"


class CardRejected(ValidationError):
    """A card op was refused (unknown op, missing --eta, title too long, bad token...)."""
    code = "card_rejected"


class PolicyDenied(DeckError):
    """Account policy refused the pairing (design §2.3), exit 3."""
    exit_code = 3
    code = "policy_denied"


class IllegalTransition(DeckError):
    """No row of the §5.2 table allows this move, or its guard failed (exit 4)."""
    exit_code = 4
    code = "illegal"


class StaleRev(IllegalTransition):
    """The caller's rev does not match the stored rev (optimistic lock, exit 4)."""
    code = "stale"


class NotFound(DeckError):
    exit_code = 5
    code = "not_found"


class DomainBusy(DeckError):
    exit_code = 6
    code = "domain_busy"
