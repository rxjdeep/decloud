"""Who is using this machine: the name shown in the prompt at the top of the home screen (name@reclmr)."""
import re

from flask import Blueprint, jsonify, request

import shared

bp = Blueprint('profile', __name__)


def clean_user_name(raw):
    """A name that reads well in a terminal prompt: lowercase, spaces become underscores, letters, digits and . _ - only."""
    if not isinstance(raw, str):
        return ''
    s = re.sub(r'\s+', '_', raw.strip().lower())
    s = re.sub(r'[^a-z0-9._-]', '', s)
    s = re.sub(r'_{2,}', '_', s).strip('._-')
    return s[:20]


@bp.route('/api/profile', methods=['GET', 'POST'])
def profile():
    settings = shared.load_settings()
    prof = dict(settings.get('profile') or {})
    if request.method == 'GET':
        return jsonify({'name': prof.get('name', '')})
    data = request.get_json(silent=True) or {}
    name = clean_user_name(data.get('name'))
    if not name:
        return jsonify({'error': 'Use letters, numbers, dots, dashes or underscores'}), 400
    prof['name'] = name
    settings['profile'] = prof
    if not shared.save_settings(settings):
        return jsonify({'error': 'Could not save'}), 500
    return jsonify({'name': name})
