"""Versioned, bounded intake contracts. A contract is scope, not authorization."""
import re
from agentshield_batches import normalize
from agentshield_campaigns import digest


def create(targets, selected_groups, rigor, categories, available, request_key, intent='launch'):
    if intent not in ('setup', 'launch'):
        raise ValueError('INVALID_HANDOFF_INTENT')
    if not isinstance(request_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', request_key):
        raise ValueError('INVALID_REQUEST_KEY')
    specs = normalize({'targets': targets, 'groups': selected_groups, 'rigor': rigor, 'categories': categories}, available)
    result = {'version': 1, 'intent': intent, 'request_key': request_key,
              'targets': [spec['target'] for spec in specs], 'rigor': rigor,
              'categories': specs[0]['categories'],
              'total_cases': sum(spec['expected_security_cases'] + 1 for spec in specs)}
    return {**result, 'scope_hash': digest(result)}


def validate(value, available):
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1:
        raise ValueError('INVALID_HANDOFF_VERSION')
    expected = create(value.get('targets'), [], value.get('rigor'), value.get('categories'),
                      available, value.get('request_key'), value.get('intent'))
    if value != expected:
        raise ValueError('HANDOFF_SCOPE_MISMATCH')
    return expected