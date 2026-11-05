"""Published native limits; live catalog values take precedence."""
from copy import deepcopy
import re

DEEPSEEK_ID = 'deepseek-v4-1-flash-1017'


def is_removed_model(model):
    return model.removesuffix('[1m]') == DEEPSEEK_ID


def published_limits(model):
    name = re.sub(r'[^a-z0-9]', '', model.lower())
    if 'glm53' in name:
        return {'max_context_length': 1_000_000, 'max_output_tokens': 128_000}
    if 'mistrallarge4' in name:
        return {'max_context_length': 1_000_000}
    return {}


def prepare_catalog(catalog):
    result = deepcopy([entry for entry in catalog if not is_removed_model(entry.get('id', ''))])
    for entry in result:
        for key, value in published_limits(entry['id']).items():
            current = entry.get(key)
            if type(current) is not int or current <= 0:
                entry[key] = value
    return result


def output_limit(entry):
    value = entry.get('max_output_tokens')
    if type(value) is int and value > 0:
        return value
    return published_limits(entry['id']).get('max_output_tokens')
