"""Provider-neutral contract and validation for invoice interpretation results."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Protocol


_INVOICE_FIELDS = (
    "invoice_number",
    "client",
    "issued",
    "due",
    "currency",
    "subtotal",
    "tax",
    "discount",
    "total",
    "line_items",
)
_LINE_FIELDS = ("description", "quantity", "unit_price", "line_total")
_STRING_FIELDS = ("invoice_number", "client", "currency")
_DATE_FIELDS = ("issued", "due")
_DECIMAL_FIELDS = ("subtotal", "tax", "discount", "total")
_VALUE_TOKEN = re.compile(r"(?<![\w.])[+-]?\d+(?:\.\d+)?(?![\w.])")


class InvoiceInterpretationError(ValueError):
    """Raised when a provider response violates the interpretation contract."""


@dataclass(frozen=True)
class InvoiceInterpretationRequest:
    """Source evidence and deterministic findings supplied to an interpreter."""

    source_text: str
    deterministic_invoice: Mapping[str, Any]
    missing_fields: tuple[str, ...]


@dataclass(frozen=True)
class ValidatedInvoiceInterpretation:
    """Schema-validated invoice values, converted to safe Python types."""

    invoice: dict[str, Any]
    evidence: dict[str, str]


@dataclass(frozen=True)
class InvoiceMergeResult:
    """Merged values and conflicts; callers must not proceed when conflicts exist."""

    invoice: dict[str, Any]
    conflicts: tuple[str, ...]
    missing_fields: tuple[str, ...]

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)


class InvoiceInterpreter(Protocol):
    """Provider interface. Implementations may be local or explicitly enabled."""

    def interpret(
        self, request: InvoiceInterpretationRequest
    ) -> str | Mapping[str, Any]:
        """Return a JSON response or its decoded object without side effects."""


def interpret_invoice(
    interpreter: InvoiceInterpreter,
    source_text: str,
    deterministic_invoice: Mapping[str, Any],
    missing_fields: tuple[str, ...] | list[str],
) -> ValidatedInvoiceInterpretation:
    """Call an injected interpreter and validate its response against source text."""
    if not isinstance(source_text, str):
        raise TypeError("source_text must be a string")
    request = InvoiceInterpretationRequest(
        source_text=source_text,
        deterministic_invoice=deterministic_invoice,
        missing_fields=tuple(missing_fields),
    )
    response = interpreter.interpret(request)
    return validate_invoice_interpretation(response, source_text)


def validate_invoice_interpretation(
    response: str | Mapping[str, Any], source_text: str
) -> ValidatedInvoiceInterpretation:
    """Validate exact schema, types, and literal textual evidence."""
    if not isinstance(source_text, str):
        raise TypeError("source_text must be a string")
    if isinstance(response, str):
        try:
            payload = json.loads(
                response,
                parse_float=Decimal,
                parse_int=Decimal,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, InvalidOperation, ValueError) as exc:
            raise InvoiceInterpretationError("Resposta não contém JSON válido.") from exc
    elif isinstance(response, Mapping):
        payload = dict(response)
    else:
        raise InvoiceInterpretationError("Resposta deve ser JSON ou objeto.")

    if not isinstance(payload, Mapping):
        raise InvoiceInterpretationError("Resposta JSON deve ser um objeto.")
    _require_exact_keys(payload, {"schema_version", "invoice", "evidence"}, "raiz")
    if payload["schema_version"] != "1" or not isinstance(
        payload["schema_version"], str
    ):
        raise InvoiceInterpretationError('schema_version deve ser "1".')

    invoice_payload = payload["invoice"]
    if not isinstance(invoice_payload, Mapping):
        raise InvoiceInterpretationError("invoice deve ser um objeto.")
    _require_exact_keys(invoice_payload, set(_INVOICE_FIELDS), "invoice")
    evidence_payload = payload["evidence"]
    if not isinstance(evidence_payload, Mapping):
        raise InvoiceInterpretationError("evidence deve ser um objeto.")

    normalized: dict[str, Any] = {
        field: None for field in _INVOICE_FIELDS if field != "line_items"
    }
    raw_line_items = invoice_payload["line_items"]
    if not isinstance(raw_line_items, list):
        raise InvoiceInterpretationError("invoice.line_items deve ser uma lista.")
    normalized["line_items"] = []

    expected_evidence_keys: set[str] = set()
    for field in _INVOICE_FIELDS:
        if field == "line_items":
            continue
        value = invoice_payload[field]
        if value is None:
            continue
        expected_evidence_keys.add(field)
        normalized[field] = _validate_scalar(field, value)

    for index, raw_item in enumerate(raw_line_items):
        path = f"line_items[{index}]"
        if not isinstance(raw_item, Mapping):
            raise InvoiceInterpretationError(f"{path} deve ser um objeto.")
        _require_exact_keys(raw_item, set(_LINE_FIELDS), path)
        item: dict[str, Any] = {}
        for field in _LINE_FIELDS:
            value = raw_item[field]
            item[field] = None if value is None else _validate_line_scalar(
                f"{path}.{field}", value
            )
            if value is not None:
                expected_evidence_keys.add(f"{path}.{field}")
        normalized["line_items"].append(item)

    _require_exact_keys(
        evidence_payload, expected_evidence_keys, "evidence"
    )
    normalized_evidence: dict[str, str] = {}
    for path in expected_evidence_keys:
        witness = evidence_payload[path]
        if not isinstance(witness, str) or not witness.strip():
            raise InvoiceInterpretationError(f"Evidência de {path} deve ser texto.")
        if witness not in source_text:
            raise InvoiceInterpretationError(
                f"Evidência de {path} não aparece literalmente no texto-fonte."
            )
        value = _value_at_path(normalized, path)
        _require_value_supported_by_evidence(path, value, witness)
        normalized_evidence[path] = witness

    return ValidatedInvoiceInterpretation(
        invoice=normalized,
        evidence=normalized_evidence,
    )


def merge_invoice_interpretations(
    deterministic_invoice: Mapping[str, Any],
    ai_result: ValidatedInvoiceInterpretation,
) -> InvoiceMergeResult:
    """Merge validated AI values without overriding deterministic findings."""
    merged = {
        field: deterministic_invoice.get(field)
        for field in _INVOICE_FIELDS
    }
    conflicts: list[str] = []
    for field in _INVOICE_FIELDS:
        deterministic_value = merged[field]
        interpreted_value = ai_result.invoice[field]
        if field == "line_items":
            deterministic_lines = deterministic_value or []
            interpreted_lines = interpreted_value or []
            if deterministic_value is None:
                merged[field] = interpreted_lines if interpreted_value else None
            elif interpreted_value and deterministic_lines != interpreted_lines:
                conflicts.append(field)
            continue
        if deterministic_value is None:
            if interpreted_value is not None:
                merged[field] = interpreted_value
        elif interpreted_value is not None and not _values_equal(
            field, deterministic_value, interpreted_value
        ):
            conflicts.append(field)

    missing: list[str] = [
        field for field in _INVOICE_FIELDS
        if field != "line_items" and merged[field] is None
    ]
    line_items = merged["line_items"]
    if not line_items:
        missing.append("line_items")
    else:
        for index, item in enumerate(line_items):
            missing.extend(
                f"line_items[{index}].{field}"
                for field in _LINE_FIELDS
                if item.get(field) is None
            )
    return InvoiceMergeResult(
        invoice=merged,
        conflicts=tuple(conflicts),
        missing_fields=tuple(missing),
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON constant {value} is not allowed")


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], context: str
) -> None:
    actual = set(value)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        details = []
        if missing:
            details.append(f"faltam {sorted(missing)}")
        if extra:
            details.append(f"desconhecidos {sorted(extra)}")
        raise InvoiceInterpretationError(
            f"Campos inválidos em {context}: {'; '.join(details)}."
        )


def _validate_scalar(field: str, value: Any) -> Any:
    if field in _STRING_FIELDS:
        if not isinstance(value, str) or not value.strip():
            raise InvoiceInterpretationError(f"{field} deve ser texto não vazio.")
        return value.strip()
    if field in _DATE_FIELDS:
        if not isinstance(value, str):
            raise InvoiceInterpretationError(f"{field} deve ser data ISO em texto.")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise InvoiceInterpretationError(f"{field} contém data inválida.") from exc
        if parsed.isoformat() != value:
            raise InvoiceInterpretationError(f"{field} deve usar formato ISO YYYY-MM-DD.")
        return parsed
    if field in _DECIMAL_FIELDS:
        return _to_decimal(field, value)
    raise InvoiceInterpretationError(f"Campo não permitido: {field}.")


def _validate_line_scalar(path: str, value: Any) -> Any:
    field = path.rsplit(".", 1)[1]
    if field == "description":
        if not isinstance(value, str) or not value.strip():
            raise InvoiceInterpretationError(f"{path} deve ser texto não vazio.")
        return value.strip()
    if field in ("quantity", "unit_price", "line_total"):
        return _to_decimal(path, value)
    raise InvoiceInterpretationError(f"Campo não permitido: {path}.")


def _to_decimal(path: str, value: Any) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise InvoiceInterpretationError(f"{path} não aceita float ou boolean.")
    if not isinstance(value, (Decimal, int, str)):
        raise InvoiceInterpretationError(f"{path} deve ser um decimal seguro.")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise InvoiceInterpretationError(f"{path} contém decimal inválido.") from exc
    if not result.is_finite():
        raise InvoiceInterpretationError(f"{path} deve ser finito.")
    return result


def _value_at_path(invoice: Mapping[str, Any], path: str) -> Any:
    match = re.fullmatch(r"line_items\[(\d+)\]\.(description|quantity|unit_price|line_total)", path)
    if match:
        return invoice["line_items"][int(match.group(1))][match.group(2)]
    return invoice[path]


def _require_value_supported_by_evidence(
    path: str, value: Any, witness: str
) -> None:
    if isinstance(value, str):
        if value.casefold() not in witness.casefold():
            raise InvoiceInterpretationError(
                f"Evidência de {path} não contém o valor informado."
            )
    elif isinstance(value, date):
        if value.isoformat() not in witness:
            raise InvoiceInterpretationError(
                f"Evidência de {path} não contém a data ISO informada."
            )
    elif isinstance(value, Decimal):
        tokens = _VALUE_TOKEN.findall(witness)
        supported = False
        for token in tokens:
            try:
                if Decimal(token) == value:
                    supported = True
                    break
            except InvalidOperation:
                continue
        if not supported:
            raise InvoiceInterpretationError(
                f"Evidência de {path} não contém o valor decimal informado."
            )


def _values_equal(field: str, left: Any, right: Any) -> bool:
    if field in _DECIMAL_FIELDS:
        try:
            return Decimal(left) == Decimal(right)
        except (InvalidOperation, TypeError, ValueError):
            return False
    return left == right
