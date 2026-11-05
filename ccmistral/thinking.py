"""Model-specific reasoning settings, sent using Mistral's wire values."""
import re


def levels_for(model, entry=None):
    # User-provided levels, shared by GLM 5.3 and its Flash variant.
    normalized = re.sub(r'[^a-z0-9]', '', model.lower())
    label = re.sub(r'[^a-z0-9]', '', (entry or {}).get('label', '').lower())
    names = (normalized, label)
    if any('glm53' in name for name in names):
        return ['low', 'high', 'max']
    if any('mistrallarge4' in name for name in names):
        return ['low', 'medium', 'high']
    # Other models can advertise their own supported wire values in Vibe's catalog.
    advertised = ((entry or {}).get('parameters') or {}).get('reasoning')
    if isinstance(advertised, list):
        return list(dict.fromkeys(level for level in advertised if isinstance(level, str) and level.strip()))
    return []


def validate(model, level, entry=None):
    if level is not None and level not in levels_for(model, entry):
        supported = ', '.join(levels_for(model, entry)) or 'none reported for this model'
        raise ValueError(f'Unsupported thinking level {level!r} for {model}. Supported: {supported}')
    return level


def request_effort(body, fallback=None):
    """Resolve Claude's effort, including its newest per-turn system setting."""
    effort = fallback
    config = body.get('output_config') or {}
    if 'effort' in config:
        effort = config['effort']
    for message in body.get('messages', []):
        if message.get('role') == 'system':
            config = message.get('output_config') or {}
            if 'effort' in config:
                effort = config['effort']
    return effort
