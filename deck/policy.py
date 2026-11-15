"""Account policy (design §2.3). A pure function; no configuration can override the hard rows.

| account \\ project | personal | office                                      |
| personal          | allow    | allow iff project.allowPersonalAccounts     |
| office            | DENY     | allow                                       |
| untagged          | deny     | deny                                        |
| any, project untagged → deny                                                |
"""
SCOPES = ("office", "personal")

OFFICE_NEVER_PERSONAL = "office account may never run personal projects"
NO_PERSONAL_ACCOUNTS = "office project does not allow personal accounts"
ACCOUNT_UNTAGGED = "account has no scope tag"
PROJECT_UNTAGGED = "project has no scope tag"
NO_ACCOUNT = "unknown account"


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def allow(account_cfg, project_cfg) -> tuple[bool, str]:
    """Return (allowed, reason). Fails closed on anything missing or unexpected."""
    if account_cfg is None:
        return False, NO_ACCOUNT
    a = _get(account_cfg, "scope")
    p = _get(project_cfg, "scope")
    if a not in SCOPES:
        return False, ACCOUNT_UNTAGGED
    if p not in SCOPES:
        return False, PROJECT_UNTAGGED
    if a == "office":
        if p == "personal":
            return False, OFFICE_NEVER_PERSONAL  # hard-coded: never configurable
        return True, "office account on office project"
    # personal account
    if p == "personal":
        return True, "personal account on personal project"
    if _get(project_cfg, "allowPersonalAccounts", False) is True:
        return True, "office project allows personal accounts"
    return False, NO_PERSONAL_ACCOUNTS
