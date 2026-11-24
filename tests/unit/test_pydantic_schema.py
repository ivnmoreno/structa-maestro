"""Unit tests for Pydantic v2 schema derivation and lossless JSON export (T005, T007)."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum, IntEnum, StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from structa_maestro import (
    ArithmeticRule,
    ArithmeticTerm,
    ComparisonOperator,
    ComparisonRule,
    ConditionalPresenceKind,
    ConditionalPresenceRule,
    ConditionOperator,
    ExtractionCardinality,
    ExtractionSchema,
    FieldCondition,
    FieldType,
    GroupPresenceKind,
    GroupPresenceRule,
    RuleSeverity,
    SchemaDefinitionError,
)


class Currency(StrEnum):
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"


class PriorityCode(IntEnum):
    LOW = 1
    HIGH = 2


class TaxRate(Enum):
    ZERO = Decimal("0.00")
    STANDARD = Decimal("0.20")


class LineItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    sku: str = Field(min_length=2, max_length=20, pattern=r"^[A-Z0-9-]+$")
    quantity: int = Field(gt=0, lt=1000)
    unit_price: Decimal = Field(ge=Decimal("0.00"))
    line_total: Decimal = Field(ge=Decimal("0.00"))


class InvoiceModel(BaseModel):
    invoice_number: Annotated[str, Field(pattern=r"^INV-\d{4}-\d+$", description="Invoice ID")]
    vendor_name: str = Field(min_length=1, max_length=120)
    issue_date: date
    due_date: date | None = None
    currency: Currency = Currency.USD
    priority: PriorityCode = PriorityCode.LOW
    tax_rate: TaxRate = TaxRate.STANDARD
    status: Literal["draft", "final", None] = None
    is_paid: bool = False
    discount_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    subtotal: Decimal = Field(ge=0)
    tax_amount: Decimal = Field(ge=0)
    total_amount: Decimal = Field(ge=0)
    po_number: str | None = None
    wire_ref: str | None = None
    check_number: str | None = None
    line_items: list[LineItem] = Field(min_length=1, max_length=50)


def test_from_pydantic_maps_all_canonical_types_constraints_and_rules() -> None:
    rules = (
        ComparisonRule(
            rule_id="due_after_issue",
            left_field="due_date",
            operator=ComparisonOperator.GTE,
            right_field="issue_date",
        ),
        ArithmeticRule(
            rule_id="line_items_sum",
            terms=(ArithmeticTerm(field="line_items", item_field="line_total"),),
            target_field="subtotal",
            tolerance=Decimal("0.01"),
        ),
        ConditionalPresenceRule(
            rule_id="po_required_when_final",
            kind=ConditionalPresenceKind.REQUIRED_IF,
            when=FieldCondition(field="status", operator=ConditionOperator.EQUALS, value="final"),
            target_fields=("po_number",),
            severity=RuleSeverity.WARNING,
        ),
        GroupPresenceRule(
            rule_id="payment_ref_exclusive",
            kind=GroupPresenceKind.MUTUALLY_EXCLUSIVE,
            fields=("wire_ref", "check_number"),
        ),
    )

    schema = ExtractionSchema.from_pydantic(
        InvoiceModel,
        schema_id="invoice-pydantic",
        schema_version="2.0",
        cardinality=ExtractionCardinality.MULTIPLE,
        rules=rules,
    )

    assert schema.schema_id == "invoice-pydantic"
    assert schema.schema_version == "2.0"
    assert schema.cardinality == ExtractionCardinality.MULTIPLE
    assert schema.allow_additional_fields is False
    assert len(schema.rules) == 4

    f = schema.fields
    assert f["invoice_number"].field_type == FieldType.STRING
    assert f["invoice_number"].required is True
    assert f["invoice_number"].nullable is False
    assert f["invoice_number"].description == "Invoice ID"
    assert f["invoice_number"].constraints.pattern == r"^INV-\d{4}-\d+$"

    assert f["vendor_name"].constraints.min_length == 1
    assert f["vendor_name"].constraints.max_length == 120
    assert f["issue_date"].field_type == FieldType.DATE
    assert f["due_date"].field_type == FieldType.DATE
    assert f["due_date"].required is False
    assert f["due_date"].nullable is True

    assert f["currency"].field_type == FieldType.STRING
    assert f["currency"].constraints.allowed_values == ("USD", "EUR", "GBP")
    assert f["priority"].field_type == FieldType.INTEGER
    assert f["priority"].constraints.allowed_values == (1, 2)
    assert f["tax_rate"].field_type == FieldType.DECIMAL
    assert f["tax_rate"].constraints.allowed_values == (Decimal("0.00"), Decimal("0.20"))

    assert f["status"].field_type == FieldType.STRING
    assert f["status"].nullable is True
    assert f["status"].constraints.allowed_values == ("draft", "final")
    assert f["is_paid"].field_type == FieldType.BOOLEAN
    assert f["discount_pct"].field_type == FieldType.DECIMAL
    assert f["discount_pct"].constraints.min_value == Decimal("0.0")
    assert f["discount_pct"].constraints.max_value == Decimal("100.0")

    assert f["line_items"].field_type == FieldType.ARRAY
    assert f["line_items"].constraints.min_items == 1
    assert f["line_items"].constraints.max_items == 50
    item_spec = f["line_items"].items
    assert item_spec is not None
    assert item_spec.field_type == FieldType.OBJECT
    assert item_spec.allow_additional_fields is True
    assert item_spec.properties is not None
    # Integer gt=0, lt=1000 translated exactly to [1, 999]
    assert item_spec.properties["quantity"].constraints.min_value == 1
    assert item_spec.properties["quantity"].constraints.max_value == 999

    # Default schema_id uses model class name; explicit allow_additional_fields overrides config
    default_schema = ExtractionSchema.from_pydantic(LineItem, allow_additional_fields=False)
    assert default_schema.schema_id == "LineItem"
    assert default_schema.allow_additional_fields is False


def test_from_pydantic_rejects_unsupported_types_constraints_aliases_and_cycles() -> None:
    # 1. Non-BaseModel / empty model
    with pytest.raises(SchemaDefinitionError):
        ExtractionSchema.from_pydantic(dict)  # type: ignore[arg-type]

    class EmptyModel(BaseModel):
        pass

    with pytest.raises(SchemaDefinitionError):
        ExtractionSchema.from_pydantic(EmptyModel)

    # 2. datetime.datetime rejected (Contract v1.0 supports date only)
    class WithDatetime(BaseModel):
        created_at: datetime

    with pytest.raises(SchemaDefinitionError, match="datetime"):
        ExtractionSchema.from_pydantic(WithDatetime)

    # 3. Any, non-optional Union, dict, set, custom class
    class WithAny(BaseModel):
        payload: Any

    with pytest.raises(SchemaDefinitionError):
        ExtractionSchema.from_pydantic(WithAny)

    class WithUnion(BaseModel):
        code: str | int

    with pytest.raises(SchemaDefinitionError, match="Union"):
        ExtractionSchema.from_pydantic(WithUnion)

    class WithDict(BaseModel):
        meta: dict[str, str]

    with pytest.raises(SchemaDefinitionError):
        ExtractionSchema.from_pydantic(WithDict)

    class CustomObj:
        pass

    class WithCustom(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)
        obj: CustomObj

    with pytest.raises(SchemaDefinitionError):
        ExtractionSchema.from_pydantic(WithCustom)

    # 4. Mixed Literal / Literal[None] / Empty Enum / Mixed Enum
    class WithMixedLiteral(BaseModel):
        x: Literal["a", 1]

    with pytest.raises(SchemaDefinitionError):
        ExtractionSchema.from_pydantic(WithMixedLiteral)

    class WithNoneLiteral(BaseModel):
        x: Literal[None]

    with pytest.raises(SchemaDefinitionError):
        ExtractionSchema.from_pydantic(WithNoneLiteral)

    class MixedEnum(Enum):
        A = "a"
        B = 2

    class WithMixedEnum(BaseModel):
        e: MixedEnum

    with pytest.raises(SchemaDefinitionError):
        ExtractionSchema.from_pydantic(WithMixedEnum)

    # 5. Non-integer strict exclusive gt/lt & multiple_of
    class WithDecimalGt(BaseModel):
        amount: Decimal = Field(gt=Decimal("0"))

    with pytest.raises(SchemaDefinitionError, match="gt"):
        ExtractionSchema.from_pydantic(WithDecimalGt)

    class WithFloatLt(BaseModel):
        ratio: float = Field(lt=1.0)

    with pytest.raises(SchemaDefinitionError, match="lt"):
        ExtractionSchema.from_pydantic(WithFloatLt)

    class WithMultipleOf(BaseModel):
        step: int = Field(multiple_of=5)

    with pytest.raises(SchemaDefinitionError, match="multiple_of"):
        ExtractionSchema.from_pydantic(WithMultipleOf)

    # 6. Differing alias / validation_alias
    class WithAlias(BaseModel):
        invoice_no: str = Field(alias="invoiceNumber")

    with pytest.raises(SchemaDefinitionError, match="alias"):
        ExtractionSchema.from_pydantic(WithAlias)

    class WithValAlias(BaseModel):
        invoice_no: str = Field(validation_alias="inv_no")

    with pytest.raises(SchemaDefinitionError, match="validation_alias"):
        ExtractionSchema.from_pydantic(WithValAlias)

    # 7. Cyclic / self-referential model & max_depth overflow
    class TreeNode(BaseModel):
        name: str
        child: TreeNode | None = None

    TreeNode.model_rebuild()
    with pytest.raises(SchemaDefinitionError, match="cyclic"):
        ExtractionSchema.from_pydantic(TreeNode)

    with pytest.raises(SchemaDefinitionError, match="depth"):
        ExtractionSchema.from_pydantic(InvoiceModel, max_depth=2)


def test_extraction_schema_to_dict_and_to_json_lossless_round_trip(tmp_path: Path) -> None:
    rules = (
        ComparisonRule(
            rule_id="due_after_issue",
            left_field="due_date",
            operator=ComparisonOperator.GTE,
            right_field="issue_date",
            message="Due date cannot precede issue date.",
        ),
        ArithmeticRule(
            rule_id="subtotal_plus_tax",
            terms=(
                ArithmeticTerm(field="subtotal"),
                ArithmeticTerm(field="tax_amount"),
            ),
            target_field="total_amount",
            tolerance=Decimal("0.02"),
        ),
        ConditionalPresenceRule(
            rule_id="po_when_usd",
            kind=ConditionalPresenceKind.REQUIRED_IF,
            when=FieldCondition(field="currency", operator=ConditionOperator.IN, values=("USD",)),
            target_fields=("po_number",),
        ),
        GroupPresenceRule(
            rule_id="at_least_one_ref",
            kind=GroupPresenceKind.AT_LEAST_ONE_OF,
            fields=("wire_ref", "check_number"),
            severity=RuleSeverity.WARNING,
        ),
    )

    schema = ExtractionSchema.from_pydantic(InvoiceModel, schema_id="inv-rt", rules=rules)

    as_dict = schema.to_dict()
    assert json.loads(json.dumps(as_dict)) == as_dict
    assert ExtractionSchema.from_dict(as_dict) == schema

    json_file = tmp_path / "exported_schema.json"
    json_file.write_text(schema.to_json(indent=2), encoding="utf-8")
    reloaded = ExtractionSchema.from_json_file(json_file)
    assert reloaded == schema
