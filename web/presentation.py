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
    r'\b(?:spawn_gen|endpoint_task_id|schema|HEAD)\b|'
    r'\S+\.(?:md|json|jsonl)\b|(?:/home/|/tmp/)\S+', re.I)


def excerpt(value, limit=280):
    """Keep a readable opening, without rewriting or silently translating it."""
    text = str(value or '').strip()
    if not text or text in STOCK_REASONS:
        return ''
    text = re.sub(r'^#{1,6}\s+', '', text.split('\n\n')[0])
    text = re.sub(r'\s+', ' ', text).strip()
    if TECHNICAL.search(text):
        text = re.split(r'(?<=[.!?])\s+', text)[0]
        if TECHNICAL.search(text):
            return ''
    if len(text) <= limit:
        return text
    opening = re.split(r'(?<=[.!?])\s+', text)[0]
    if len(opening) <= limit:
        return opening
    return text[:limit - 1].rsplit(' ', 1)[0] + '…'


def flow_evidence(flow):
    flow = flow if isinstance(flow, dict) else {}
    evidence = {key: flow[key] for key in ('reason', 'waiting_for', 'next_action') if key in flow}
    presentation = flow.get('presentation')
    if isinstance(presentation, dict):
        evidence['presentation'] = {key: presentation[key] for key in ('summary', 'waiting_for', 'next_action')
                                    if key in presentation}
    return evidence


def project(card, context, coordinator):
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
    reason = flow.get('reason')
    run = re.fullmatch(r'validating \((running|fixing|passed|failed)\)(?: · run: \S+)?', reason if isinstance(reason, str) else '')
    return {
        'summary': first_excerpt([human.get('summary'), reason, context.get('reason')], STOCK_REASONS),
        'next_action': first_excerpt([human.get('next_action'), flow.get('next_action')], STOCK_ACTIONS),
        'waiting_for': first_excerpt([human.get('waiting_for'), flow.get('waiting_for')], STOCK_WAITS),
        'coordinator': coordinator,
        'executor': {'harness': getattr(card, 'agent', '') or ''},
        'run_step': {'phase': 'validation', 'state': run[1]} if run else None,
        # snapshot_state can be the validation job, and Herdr status can be
        # unverified. Neither establishes native model activity.
        'activity': 'unverified',
    }
