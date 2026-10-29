"""Small, instance-scoped presentation catalog shared with the browser.

English source messages are keys. Unknown keys remain English; authored task
text must never be passed through this translator.
"""
import json
from pathlib import Path
import re

CATALOGS = Path(__file__).resolve().parent / 'locales'
# Receipt files keep the message they were saved with: earlier English and
# Spanish receipt wording maps to the current message.
RECEIPT_MESSAGES = {
    'Submission started. If the connection drops, check this receipt before retrying.':
        'Sending started. If the connection dropped, check the answer status before sending again.',
    'Envío iniciado. Si la conexión se interrumpe, verifica este recibo antes de repetir.':
        'Sending started. If the connection dropped, check the answer status before sending again.',
    'Review request saved.': 'Saved. Firstmate will check again.',
    'Solicitud de revisión guardada.': 'Saved. Firstmate will check again.',
    'Respuesta guardada por Firstmate.': 'Answer saved by Firstmate.',
    'The intake did not confirm submission. Check the receipt; your draft is preserved.':
        'Firstmate didn’t confirm it got your answer. Check the answer status; your draft is safe.',
    'El intake no confirmó el envío. Verifica el recibo; tu borrador se conserva.':
        'Firstmate didn’t confirm it got your answer. Check the answer status; your draft is safe.',
    'The occurrence changed before saving. The answer was not sent; your draft is preserved.':
        'The question changed before saving, so nothing was sent. Your draft is safe.',
    'La ocurrencia cambió antes de guardar. No se envió la respuesta; tu borrador se conserva.':
        'The question changed before saving, so nothing was sent. Your draft is safe.',
    'Could not confirm submission. Your draft is preserved.':
        'Couldn’t confirm your answer was sent. Check the answer status; your draft is safe.',
    'No se pudo confirmar el envío. Tu borrador se conserva.':
        'Couldn’t confirm your answer was sent. Check the answer status; your draft is safe.',
}


def normalize_language(value):
    # Deliberately accept a language plus optional BCP 47 region, including 419.
    # No OS/browser language discovery: a clean installation always uses English.
    tag = str(value or '').strip().replace('_', '-')
    match = re.fullmatch(r'(en|es)(?:-([a-z]{2}|[0-9]{3}))?', tag, re.I | re.ASCII)
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
        """legacy is accepted for older callers and ignored; the TUI reads these catalogs too."""
        text = self.messages.get(message, message)
        return text.format_map(values) if values else text

    def receipt_message(self, message):
        # Compatibility is restricted to known receipt chrome, never task prose.
        return self.t(RECEIPT_MESSAGES.get(message, message))

    def client(self):
        return {'locale': self.locale, 'language': self.language, 'messages': self.messages}
