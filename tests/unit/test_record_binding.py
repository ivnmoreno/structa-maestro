"""Unit tests for strict, non-coercing typed record binding (T009)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import IntEnum, StrEnum

import pytest
from pydantic import BaseModel, ConfigDict, Field

from structa_maestro import (
    ExtractedField,
    ExtractedRecord,
    ExtractionResult,
    FieldPresence,
    ProcessingStatus,
    RecordBindingError,
    SchemaMetadata,
    ValidationOutcome,
    ValidationStatus,
    bind_record,
    bind_records,
)


class Currency(StrEnum):
    USD = "USD"
    EUR = "EUR"


class Level(IntEnum):
    LOW = 1
    HIGH = 2


class Address(BaseModel):
    city: str
    zip_code: str | None = None


class OrderItem(BaseModel):
    sku: str
    qty: int = Field(ge=1)


class OrderModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str
    quantity: int
    unit_price: Decimal
    discount_rate: float = 0.0
    currency: Currency
    level: Level = Level.LOW
    ordered_on: date
    notes: str | None = "default-note"
    address: Address
    items: list[OrderItem]


def _make_field(
    value: object,
    *,
    presence: FieldPresence = FieldPresence.PRESENT,
) -> ExtractedField:
    return ExtractedField(
        presence=presence,
        value=value,
        validation=ValidationOutcome(status=ValidationStatus.VALID),
    )


def _valid_order_record(
    *,
    record_id: str = "rec-1",
    status: ProcessingStatus = ProcessingStatus.COMPLETED,
    val_status: ValidationStatus = ValidationStatus.VALID,
    overrides: dict[str, ExtractedField] | None = None,
) -> ExtractedRecord:
    fields: dict[str, ExtractedField] = {
        "order_id": _make_field("ORD-100"),
        "quantity": _make_field(3),
        "unit_price": _make_field(Decimal("19.95")),
        "discount_rate": _make_field(Decimal("0.125")),
        "currency": _make_field("USD"),
        "level": _make_field(2),
        "ordered_on": _make_field(date(2026, 4, 1)),
        "notes": _make_field(None, presence=FieldPresence.MISSING),
        "address": _make_field({"city": "Madrid", "zip_code": None}),
        "items": _make_field([{"sku": "A-1", "qty": 2}]),
    }
    if overrides:
        fields.update(overrides)
    return ExtractedRecord(
        record_id=record_id,
        status=status,
        fields=fields,
        validation=ValidationOutcome(status=val_status),
    )


def test_bind_record_succeeds_with_enum_float_decimal_and_missing_vs_null() -> None:
    # MISSING optional field 'notes' falls back to model default ("default-note")
    rec_missing = _valid_order_record()
    bound = bind_record(rec_missing, OrderModel)
    assert isinstance(bound, OrderModel)
    assert bound.order_id == "ORD-100"
    assert bound.quantity == 3
    assert bound.unit_price == Decimal("19.95")
    assert bound.discount_rate == pytest.approx(0.125)
    assert isinstance(bound.discount_rate, float)
    assert bound.currency is Currency.USD
    assert bound.level is Level.HIGH
    assert bound.ordered_on == date(2026, 4, 1)
    assert bound.notes == "default-note"
    assert bound.address == Address(city="Madrid", zip_code=None)
    assert bound.items == [OrderItem(sku="A-1", qty=2)]

    # PRESENT explicit None overrides default and binds None
    rec_null = _valid_order_record(
        overrides={
            "notes": _make_field(None, presence=FieldPresence.PRESENT),
            "unit_price": _make_field(20),  # int -> Decimal adaptation
        }
    )
    bound_null = bind_record(rec_null, OrderModel)
    assert bound_null.notes is None
    assert bound_null.unit_price == Decimal("20")


def test_bind_record_refuses_coercion_missing_required_and_non_valid_records() -> None:
    # 1. String "3" MUST NOT coerce to int 3
    rec_str_int = _valid_order_record(overrides={"quantity": _make_field("3")})
    with pytest.raises(RecordBindingError) as exc_info:
        bind_record(rec_str_int, OrderModel)
    assert exc_info.value.record_id == "rec-1"
    assert any("quantity" in issue for issue in exc_info.value.issues)

    # 2. String "2" MUST NOT coerce to IntEnum Level.HIGH; bool True MUST NOT coerce to Level.LOW
    for bad_level in ("2", True, 99):
        rec_bad_enum = _valid_order_record(overrides={"level": _make_field(bad_level)})
        with pytest.raises(RecordBindingError):
            bind_record(rec_bad_enum, OrderModel)

    # 3. String "0.5" MUST NOT coerce to float; string "19.95" MUST NOT coerce to Decimal
    rec_str_float = _valid_order_record(overrides={"discount_rate": _make_field("0.5")})
    with pytest.raises(RecordBindingError):
        bind_record(rec_str_float, OrderModel)

    rec_str_dec = _valid_order_record(overrides={"unit_price": _make_field("19.95")})
    with pytest.raises(RecordBindingError):
        bind_record(rec_str_dec, OrderModel)

    # 4. Required field marked MISSING fails binding
    rec_missing_req = _valid_order_record(
        overrides={"order_id": _make_field(None, presence=FieldPresence.MISSING)}
    )
    with pytest.raises(RecordBindingError):
        bind_record(rec_missing_req, OrderModel)

    # 5. Non-completed or non-valid record fails binding
    with pytest.raises(RecordBindingError, match="processing status"):
        bind_record(_valid_order_record(status=ProcessingStatus.PARTIAL), OrderModel)
    with pytest.raises(RecordBindingError, match="validation status"):
        bind_record(_valid_order_record(val_status=ValidationStatus.INVALID), OrderModel)

    # 6. Invalid argument types fail with RecordBindingError
    with pytest.raises(RecordBindingError):
        bind_record("not-a-record", OrderModel)  # type: ignore[arg-type]
    with pytest.raises(RecordBindingError):
        bind_record(_valid_order_record(), dict)  # type: ignore[arg-type]


def test_bind_records_default_strict_and_only_valid_filtering() -> None:
    schema_meta = SchemaMetadata(schema_id="order", schema_version="1.0")
    rec_ok = _valid_order_record(record_id="rec-ok")
    rec_bad = _valid_order_record(
        record_id="rec-bad",
        status=ProcessingStatus.COMPLETED,
        val_status=ValidationStatus.INVALID,
    )

    ok_result = ExtractionResult(
        extraction_id="ext-1",
        status=ProcessingStatus.COMPLETED,
        schema=schema_meta,
        records=[rec_ok],
        validation=ValidationOutcome(status=ValidationStatus.VALID),
    )
    bound_all = bind_records(ok_result, OrderModel)
    assert len(bound_all) == 1
    assert bound_all[0].order_id == "ORD-100"

    partial_result = ExtractionResult(
        extraction_id="ext-2",
        status=ProcessingStatus.PARTIAL,
        schema=schema_meta,
        records=[rec_ok, rec_bad],
        validation=ValidationOutcome(status=ValidationStatus.INVALID),
    )
    with pytest.raises(RecordBindingError):
        bind_records(partial_result, OrderModel)

    bound_valid_only = bind_records(partial_result, OrderModel, only_valid=True)
    assert len(bound_valid_only) == 1
    assert bound_valid_only[0].order_id == "ORD-100"

    failed_result = ExtractionResult(
        extraction_id="ext-3",
        status=ProcessingStatus.FAILED,
        schema=schema_meta,
        records=[],
        validation=ValidationOutcome(status=ValidationStatus.NOT_VALIDATED),
    )
    with pytest.raises(RecordBindingError):
        bind_records(failed_result, OrderModel)
    assert bind_records(failed_result, OrderModel, only_valid=True) == []

    with pytest.raises(RecordBindingError):
        bind_records(ok_result, OrderModel, only_valid="yes")  # type: ignore[arg-type]
