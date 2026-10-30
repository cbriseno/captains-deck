"""Human-facing projection; original producer evidence stays in technical details.

This does not classify workflow from prose or translate authored task content.
Known stock producer instructions are not authored next actions.
"""
import re


STOCK_ACTIONS = {
    'El worker continúa el encargo.',
    'Firstmate revisa las dependencias antes de continuar esta etapa.',
    'Firstmate presenta o reconcilia la decisión pendiente.',
    'Firstmate verifica la entrega y aplica la autoridad de integración configurada.',
    'Firstmate comprueba el bloqueo o la pausa; el texto declarado no demuestra que haya desaparecido.',
    'Firstmate puede despachar cuando corresponda.',
    'Firstmate necesita una observación actual; la última declaración se conserva como contexto.',
    'Firstmate revisa la condición declarada y coordina el siguiente paso.',
    'Firstmate revisa el resultado y los pendientes del encargo.',
    'Firstmate debe confirmar el estado de la tarea.',
    'Firstmate reviews the declared condition and coordinates the next step.',
    'Firstmate reviews the result and outstanding work.',
    'Firstmate must confirm the task’s state.',
}
STOCK_WAITS = {
    'revisión de la condición técnica declarada',
    'verificación e integración de Firstmate',
}
STOCK_REASONS = {'Motivo no registrado', 'No specific reason was recorded.',
                 'No se registró un motivo concreto.'}
TECHNICAL = re.compile(
    r'harness state unavailable|\b[0-9a-f]{24,}\b|\b[0-9A-HJKMNP-TV-Z]{26}\b|\brun:\s*\S+|'
    r'\b(?:spawn_gen|endpoint_task_id)\b|(?-i:\bHEAD\b)|'
    r'(?:/home/|/tmp/|~/)\S+|'
    # The agent's liveness line ("harness busy (claude-hook)") says nothing
    # about the work; neither does a shell command or a Firstmate data path.
    r'\bharness (?:busy|idle|working|waiting|stopped|exited)\b|\(\w+-hook\)|'
    r'\bsudo\s+\S|\|\s*(?:grep|tail|head|sed|awk|jq|sort)\b|'
    r'(?<![\w/.-])(?:data|state|bin)/[\w.-]+/\S*', re.I)
# A web address, without the punctuation that usually follows one in prose.
URL_RE = re.compile(r'https?://[^\s<>()\[\]]*[^\s<>()\[\].,;:!?\'"»]')
# A short commit hash in parentheses: "(af422196)".
SHORT_HASH = re.compile(r'\s*\((?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\)')
CARD_LIMITS = {'card_title': 80, 'card_status': 120}
RECORD = re.compile(
    r'(?=.*?\b(?:schema|head)"?\s*:)\{?\s*"?[\w.-]+"?\s*:\s*[^\s,{}]+'
    r'(?:(?:,\s*|\s+)"?[\w.-]+"?\s*:\s*[^\s,{}]+)*\s*\}?', re.I)
FILE_ONLY = re.compile(r'\S+\.(?:md|json|jsonl)[.,;:!?)]*(?:\s+\S+\.(?:md|json|jsonl)[.,;:!?)]*)*', re.I)


def drop_links(text):
    """Text without its web addresses or short hashes; Details keep the original."""
    text = SHORT_HASH.sub('', str(text or ''))
    text, cut = re.subn(r'(?:\b(?:PR|pull request)\s*:?\s*)?' + URL_RE.pattern, '', text, flags=re.I)
    if cut:
        # Tidy what the address leaves behind: "(… )", "word .", "Done: , ok".
        text = re.sub(r'\(\s*\)|\[\s*\]', '', text)
        text = re.sub(r'[ \t]+([.,;:!?])', r'\1', text)
        text = re.sub(r'[:;,]([.,;:!?])', r'\1', text)
        text = re.sub(r'[ \t]{2,}', ' ', text)
    return text


def plain_part(sentence):
    """The plain lead of "Plain words: technical detail" (or "; detail"), or ''."""
    head = re.split(r'[:;]\s', sentence, maxsplit=1)[0]
    found = TECHNICAL.search(sentence)
    # Only when every technical part comes after the colon.
    if (head == sentence or not found or found.start() <= len(head)
            or len(re.findall(r'[^\W\d_]{2,}', head)) < 3):
        return ''
    return head.strip() + '.'


def excerpt(value, limit=280):
    """Keep a readable opening, without rewriting or silently translating it."""
    text = str(value or '').strip()
    if not text or text in STOCK_REASONS:
        return ''
    text = re.sub(r'^#{1,6}\s+', '', drop_links(text).split('\n\n')[0])
    text = re.sub(r'\s+', ' ', text).strip()
    if FILE_ONLY.fullmatch(text) or RECORD.fullmatch(text):
        return ''
    if TECHNICAL.search(text) or len(text) > limit:
        text = re.split(r'(?<=[.!?])\s+', text)[0]
        if FILE_ONLY.fullmatch(text) or RECORD.fullmatch(text):
            return ''
        if TECHNICAL.search(text):
            return plain_part(text)
    if len(text) <= limit:
        return text
    return text[:limit - 1].rsplit(' ', 1)[0] + '…'


def card_field(value, name):
    """One of Firstmate's one-line card fields, or '' when it isn't usable."""
    if not isinstance(value, str):
        return ''
    text = ' '.join(drop_links(value).split()).strip(' ·–—-:,;')
    if not text or len(text) > CARD_LIMITS[name] or '\n' in value.strip() or TECHNICAL.search(text):
        return ''
    return text


def project(card, context):
    flow = getattr(card, 'flow', {}) or {}
    # Optional producer-authored presentation is distinct from a raw run report.
    human = flow.get('presentation')
    human = human if isinstance(human, dict) else {}
    def first_excerpt(candidates, stock):
        for value in candidates:
            text = excerpt(value) if isinstance(value, str) else ''
            if text and text not in stock:
                return text
        return ''
    # Firstmate's short card fields, written for the captain (fm-card-fields).
    card_fields = {name: card_field(human.get(name), name) for name in CARD_LIMITS}
    reason = flow.get('reason')
    run = re.fullmatch(r'validating \((running|fixing|passed|failed)\)(?: · run: \S+)?', reason if isinstance(reason, str) else '')
    return {
        'card_title': card_fields['card_title'],
        'status': card_fields['card_status'],
        'summary': first_excerpt([human.get('summary'), reason, context.get('reason')], STOCK_REASONS),
        'next_action': first_excerpt([human.get('next_action'), flow.get('next_action')], STOCK_ACTIONS),
        'waiting_for': first_excerpt([human.get('waiting_for'), flow.get('waiting_for')], STOCK_WAITS),
        'executor': {'harness': getattr(card, 'agent', '') or ''},
        'run_step': {'phase': 'validation', 'state': run[1]} if run else None,
    }
