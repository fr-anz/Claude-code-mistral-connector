"""Native Claude Code /model rows, independent of request effort."""
from .picker import choices
from .thinking import validate
from .model_limits import is_removed_model, output_limit, published_limits


def context_window(entry):
    value = entry.get('max_context_length')
    return value if type(value) is int and value > 0 else published_limits(entry['id']).get('max_context_length')


def client_model(entry):
    # Claude Code recognizes this documented suffix for gateway models.
    window = context_window(entry)
    model = entry['id']
    return model + '[1m]' if window is not None and window == 1_000_000 else model


def context_environment(catalog, selected=None):
    entry = next((entry for entry in catalog if entry.get('id') == selected), catalog[0] if catalog else {})
    result = {}
    window = context_window(entry) if entry else None
    if window:
        result['CLAUDE_CODE_MAX_CONTEXT_TOKENS'] = str(window)
    maximum = output_limit(entry) if entry else None
    if maximum:
        result['CLAUDE_CODE_MAX_OUTPUT_TOKENS'] = str(maximum)
    return result


def native_models(catalog):
    entries = {entry['id']: entry for entry in catalog if isinstance(entry, dict) and entry.get('id')}
    routes, options = {}, []
    for model, label in choices(catalog):
        if is_removed_model(model):
            continue
        routes[model] = (model, None)
        entry = entries[model]
        identifier = client_model(entry)
        routes[identifier] = (model, None)
        window = context_window(entry)
        context = f' · {window:,} token context' if window else ''
        maximum = output_limit(entry)
        if maximum:
            context += f' · {maximum:,} max output'
        options.append({'model': identifier, 'label': label,
                        'description': f'Mistral subscription · {model}{context}'})
    return routes, options


def initial_route(catalog, requested=None, thinking=None, previous=None, saved_thinking=None):
    routes, options = native_models(catalog)
    if not options:
        raise ValueError('Mistral returned no models suitable for chat and tool calls.')
    if requested and requested not in routes:
        raise ValueError(f'Model {requested!r} is not in your Mistral coding catalog.')
    requested = routes[requested][0] if requested in routes else requested
    previous = routes[previous][0] if previous in routes else previous
    recommended = next((item['id'] for item in catalog if item.get('recommended') and item['id'] in routes), None)
    model = requested or (previous if previous in routes else None) or recommended or options[0]['model']
    entry = next(item for item in catalog if item['id'] == model)
    validate(model, thinking, entry)
    # Historical picker levels must not override Claude's own per-model effort.
    return model, thinking, client_model(entry)
