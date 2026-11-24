"""structa-maestro: Structured Data Extraction Engine."""

from __future__ import annotations

from structa_maestro.batch import (
    BatchDocumentInput,
    BatchExtractionResult,
    BatchOptions,
    BatchSummary,
)
from structa_maestro.binding import bind_record, bind_records
from structa_maestro.builder.result_builder import ResultBuilder
from structa_maestro.confidence.normalizer import ConfidenceNormalizer
from structa_maestro.config import DocumentLimits, SecretValue
from structa_maestro.contracts.enums import (
    DocumentFormat,
    EvidenceRelationship,
    ExtractionCardinality,
    FieldPresence,
    FieldType,
    IssueSeverity,
    PipelineStage,
    ProcessingStatus,
    ValidationStatus,
)
from structa_maestro.contracts.models import (
    CONTRACT_VERSION,
    ConfidenceAssessment,
    DiagnosticIssue,
    ExtractedField,
    ExtractedRecord,
    ExtractionResult,
    ProvenanceEvidence,
    SchemaMetadata,
    SourceLocation,
    SourceMetadata,
    ValidationOutcome,
)
from structa_maestro.documents.inputs import DocumentInput, DocumentInputKind
from structa_maestro.documents.models import DocumentSegment, PreparedDocument
from structa_maestro.engine import (
    ExtractionEngine,
    aextract,
    aextract_batch,
    extract,
    extract_batch,
)
from structa_maestro.exceptions import (
    ConfigurationError,
    ContractDeserializationError,
    RecordBindingError,
    SchemaDefinitionError,
    StructaMaestroError,
)
from structa_maestro.provenance.associator import ProvenanceAssociator
from structa_maestro.schema.models import ExtractionSchema, FieldConstraints, FieldSpec
from structa_maestro.schema.rules import (
    ArithmeticRule,
    ArithmeticTerm,
    ComparisonOperator,
    ComparisonRule,
    ConditionalPresenceKind,
    ConditionalPresenceRule,
    ConditionOperator,
    FieldCondition,
    GroupPresenceKind,
    GroupPresenceRule,
    RuleSeverity,
    ValidationRule,
)
from structa_maestro.strategies.base import (
    AsyncExtractionStrategy,
    ExtractionStrategy,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
)
from structa_maestro.strategies.llm_config import (
    InputBudgetOverflowPolicy,
    InputBudgetPolicy,
    RetryPolicy,
    StructuredOutputMode,
)
from structa_maestro.strategies.llm_openai_compat import (
    AsyncOpenAICompatibleLLMStrategy,
    OpenAICompatibleLLMStrategy,
)
from structa_maestro.strategies.schema_compiler import (
    CompiledOutputSchema,
    compile_extraction_output_schema,
)
from structa_maestro.strategies.testing import (
    AsyncDeterministicTestStrategy,
    DeterministicTestStrategy,
)
from structa_maestro.validation.validator import DeterministicValidator

__version__ = "0.1.0"

__all__ = [
    "CONTRACT_VERSION",
    "ArithmeticRule",
    "ArithmeticTerm",
    "AsyncDeterministicTestStrategy",
    "AsyncExtractionStrategy",
    "AsyncOpenAICompatibleLLMStrategy",
    "BatchDocumentInput",
    "BatchExtractionResult",
    "BatchOptions",
    "BatchSummary",
    "ComparisonOperator",
    "ComparisonRule",
    "CompiledOutputSchema",
    "ConditionOperator",
    "ConditionalPresenceKind",
    "ConditionalPresenceRule",
    "ConfidenceAssessment",
    "ConfidenceNormalizer",
    "ConfigurationError",
    "ContractDeserializationError",
    "DeterministicTestStrategy",
    "DeterministicValidator",
    "DiagnosticIssue",
    "DocumentFormat",
    "DocumentInput",
    "DocumentInputKind",
    "DocumentLimits",
    "DocumentSegment",
    "EvidenceRelationship",
    "ExtractedField",
    "ExtractedRecord",
    "ExtractionCardinality",
    "ExtractionEngine",
    "ExtractionResult",
    "ExtractionSchema",
    "ExtractionStrategy",
    "FieldCondition",
    "FieldConstraints",
    "FieldPresence",
    "FieldSpec",
    "FieldType",
    "GroupPresenceKind",
    "GroupPresenceRule",
    "InputBudgetOverflowPolicy",
    "InputBudgetPolicy",
    "IssueSeverity",
    "OpenAICompatibleLLMStrategy",
    "PipelineStage",
    "PreparedDocument",
    "ProcessingStatus",
    "ProposedEvidence",
    "ProposedField",
    "ProposedRecord",
    "ProvenanceAssociator",
    "ProvenanceEvidence",
    "RecordBindingError",
    "ResultBuilder",
    "RetryPolicy",
    "RuleSeverity",
    "SchemaDefinitionError",
    "SchemaMetadata",
    "SecretValue",
    "SourceLocation",
    "SourceMetadata",
    "StrategyOutput",
    "StructaMaestroError",
    "StructuredOutputMode",
    "ValidationOutcome",
    "ValidationRule",
    "ValidationStatus",
    "__version__",
    "aextract",
    "aextract_batch",
    "bind_record",
    "bind_records",
    "compile_extraction_output_schema",
    "extract",
    "extract_batch",
]
