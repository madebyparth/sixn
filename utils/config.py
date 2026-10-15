import os

# Values that mean "not filled in yet" in .env / .env.example
_PLACEHOLDERS = {'', 'replace_me', 'changeme', 'todo'}


def env_value(key):
    """Env var value, or '' when it is unset or still a placeholder (REPLACE_ME, your_..._here)."""
    value = (os.environ.get(key) or '').strip()
    lowered = value.lower()
    if lowered in _PLACEHOLDERS or (lowered.startswith('your_') and lowered.endswith('_here')):
        return ''
    return value


def env_flag(key, default=False):
    value = (os.environ.get(key) or '').strip().lower()
    if not value:
        return default
    return value in ('1', 'true', 'yes', 'on')
