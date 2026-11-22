"""structa-maestro: Structured Data Extraction Engine."""

from __future__ import annotations

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
from structa_maestro.documents.models import DocumentSegment, PreparedDocument
from structa_maestro.engine import ExtractionEngine, extract
from structa_maestro.exceptions import (
    ConfigurationError,
    ContractDeserializationError,
    SchemaDefinitionError,
    StructaMaestroError,
)
from structa_maestro.provenance.associator import ProvenanceAssociator
from structa_maestro.schema.models import ExtractionSchema, FieldConstraints, FieldSpec
from structa_maestro.strategies.base import (
    ExtractionStrategy,
    ProposedEvidence,
    ProposedField,
    ProposedRecord,
    StrategyOutput,
)
from structa_maestro.strategies.llm_openai_compat import OpenAICompatibleLLMStrategy
from structa_maestro.strategies.testing import DeterministicTestStrategy
from structa_maestro.validation.validator import DeterministicValidator

__version__ = "0.1.0"

__all__ = [
    "CONTRACT_VERSION",
    "ConfidenceAssessment",
    "ConfidenceNormalizer",
    "ConfigurationError",
    "ContractDeserializationError",
    "DeterministicTestStrategy",
    "DeterministicValidator",
    "DiagnosticIssue",
    "DocumentFormat",
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
    "FieldConstraints",
    "FieldPresence",
    "FieldSpec",
    "FieldType",
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
    "ResultBuilder",
    "SchemaDefinitionError",
    "SchemaMetadata",
    "SecretValue",
    "SourceLocation",
    "SourceMetadata",
    "StrategyOutput",
    "StructaMaestroError",
    "ValidationOutcome",
    "ValidationStatus",
    "__version__",
    "extract",
]
