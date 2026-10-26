"""Small, instance-scoped presentation catalog shared with the browser.

English source messages are keys. Unknown keys remain English; authored task
text must never be passed through this translator.
"""
import json
from pathlib import Path
import re

CATALOGS = Path(__file__).resolve().parent / 'locales'


def normalize_language(value):
    # Deliberately accept a language plus optional BCP 47 region, including 419.
    # No OS/browser language discovery: a clean installation always uses English.
    tag = str(value or '').strip().replace('_', '-')
    match = re.fullmatch(r'(en|es)(?:-([a-z]{2}|[0-9]{3}))?', tag, re.I)
    if not match:
        return 'en'
    return match[1].lower() + ('-' + match[2].upper() if match[2] else '')


class Localization:
    def __init__(self, language='en'):
        self.locale = normalize_language(language)
        self.language = self.locale.split('-')[0]
        self.messages = json.loads((CATALOGS / 'en.json').read_text(encoding='utf-8'))
        if self.language != 'en':
            self.messages.update(json.loads((CATALOGS / (self.language + '.json')).read_text(encoding='utf-8')))

    def t(self, message, legacy=None, **values):
        """legacy preserves shared TUI wording; only web calls use catalogs."""
        text = self.messages.get(message, message)
        return text.format_map(values) if values else text

    def receipt_message(self, message):
        keys = (
            'Submission started. If the connection drops, check this receipt before retrying.',
            'Review request saved.', 'Answer saved by Firstmate.',
            'The intake did not confirm submission. Check the receipt; your draft is preserved.',
            'The occurrence changed before saving. The answer was not sent; your draft is preserved.',
            'Could not confirm submission. Your draft is preserved.',
        )
        # Compatibility is restricted to known receipt chrome, never task prose.
        spanish = json.loads((CATALOGS / 'es.json').read_text(encoding='utf-8'))
        key = next((k for k in keys if spanish.get(k) == message), message)
        return self.t(key)

    def client(self):
        return {'locale': self.locale, 'language': self.language, 'messages': self.messages}
