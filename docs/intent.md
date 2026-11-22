# INTENT.md — structa-maestro

**Version:** 1.2\
**Status:** Ready for `/speckit.specify`\
**Project:** structa-maestro\
**Product category:** Structured Data Extraction Engine\
**Primary deliverable:** A reusable Python library for extracting validated, schema-conforming structured records from unstructured documents.

---

## 1. Project Identity

### 1.1 Project Name

`structa-maestro`

### 1.2 Product Identity

structa-maestro is a structured data extraction engine designed to transform unstructured documents into validated, machine-readable records.

It accepts documents and user-defined schemas, identifies relevant information, extracts corresponding values, associates extracted information with source evidence, evaluates uncertainty, and validates the resulting records against the requested schema.

Its primary output is structured data, not documents, document chunks, embeddings, or search indexes.

### 1.3 Software Classification

- Primary identity: Software library / extraction engine.
- Secondary identity: Developer tool.
- Distribution: Python package.
- Optional interface: Command-line interface (CLI).

The project should be designed as a reusable software component rather than as a standalone web application or an end-user document management platform.

### 1.4 Intended Positioning

structa-maestro should demonstrate sound engineering practices in:

- Structured data extraction.
- Schema-driven information processing.
- Data validation and integrity.
- Evidence traceability and provenance.
- Uncertainty representation.
- Error handling and partial-result management.
- Extensible extraction strategies.
- Reproducible testing and evaluation.

---

## 2. Objective

The objective of structa-maestro is to provide a reliable, reusable, and extensible mechanism for extracting structured information from unstructured documents.

Given a document and an expected data schema, the engine should:

1. Process the document sufficiently to make its content available for extraction.
2. Interpret the requested schema and its extraction requirements.
3. Identify information relevant to the requested fields.
4. Extract values without inventing unsupported information.
5. Associate extracted values with source evidence wherever available.
6. Represent extraction confidence and uncertainty explicitly.
7. Validate the resulting data against the expected structure and applicable constraints.
8. Report errors, warnings, missing information, and incomplete results.
9. Produce a consistent, machine-readable output conforming to a canonical result contract.

The engine must distinguish between successfully extracting a value, having evidence to support that value, and validating that value against the requested schema.

These are separate responsibilities and must not be represented as interchangeable quality indicators.

---

## 3. Expected Outcomes

The project should deliver the following outcomes.

### 3.1 Structured Extraction

Users can define the information they want to extract and receive structured records conforming to the requested schema.

### 3.2 Traceable Results

Extracted values can be associated with their originating document and, where available, specific source locations and supporting evidence.

### 3.3 Explicit Uncertainty

The system can distinguish between:

- Information confidently extracted.
- Information extracted with uncertainty.
- Information that is ambiguous.
- Information that is missing.
- Information that cannot be supported by available evidence.

### 3.4 Reliable Validation

Results are checked against the expected schema and applicable field constraints.

Invalid or incomplete information must be reported rather than silently accepted as valid.

### 3.5 Consistent Output

All extraction strategies must produce results through the same canonical output record contract.

Users should not need to understand the internal implementation or the extraction provider to consume the results.

### 3.6 Reusable Software

The engine should be consumable programmatically by other Python applications and should not require a web application, database, or external orchestration platform.

---

## 4. Evidence of Need and Product Rationale

Unstructured documents frequently contain information that must eventually be represented in structured systems.

Examples include:

- Invoices.
- Contracts.
- Employment documents.
- Application forms.
- Business reports.
- Customer records.
- Financial documents.
- Administrative correspondence.
- Technical documentation.

Manually transferring information from these documents into structured records is time-consuming and can introduce inconsistencies and errors.

Existing extraction approaches may also produce results that are difficult to trust because they do not adequately distinguish between extracted values, source evidence, uncertainty, and schema validity.

structa-maestro addresses this problem by treating extraction as a structured data processing operation rather than simply generating JSON from text.

The resulting data should be traceable, inspectable, and suitable for downstream programmatic use.

---

## 5. Stakeholders and Users

### 5.1 Primary Users

- Python developers integrating document extraction into applications.
- Data engineers processing information from unstructured sources.
- AI engineers building structured extraction workflows.
- Developers building automation systems that consume document-derived data.

### 5.2 Secondary Users

- Organizations processing recurring document types.
- Developers building administrative and business information systems.
- Researchers and technical teams evaluating extraction approaches.

### 5.3 User Expectations

Users should be able to:

- Define the structure of the information they require.
- Submit a document for extraction.
- Obtain structured results.
- Identify the source of extracted values.
- Understand which fields are uncertain or missing.
- Determine whether records passed validation.
- Programmatically consume results and handle failures.

The initial release should prioritize developer usability and predictable behavior over end-user interface features.

---

## 6. Functional Scope

### 6.1 Long-Term Product Scope

The long-term product may support:

- Multiple document formats.
- Multiple extraction strategies.
- Different extraction providers.
- Complex schemas and nested records.
- Batch document processing.
- Advanced field-level validation.
- Cross-field consistency checks.
- Human review and correction workflows.
- Confidence calibration.
- Extraction history and reproducibility.
- Advanced evaluation and regression testing.
- Extensible processing components.

These are potential future capabilities, not commitments for the initial release.

### 6.2 Minimum Viable Product (MVP)

The MVP must establish a complete and usable extraction workflow.

#### A. Document Input

Support individual documents in these formats:

- TXT.
- Text-based PDF.
- DOCX.

The initial implementation should process documents containing extractable text.

OCR and image-based document recognition are outside the MVP.

#### B. Schema-Driven Extraction

Users must be able to provide a schema describing the expected output.

The MVP should support:

- Required and optional fields.
- Common scalar types.
- Basic nested structures.
- Basic field constraints.
- Single-record extraction.
- Multiple-record extraction from one document where applicable.

The schema must provide an explicit description of the expected information and its structure.

#### C. Extraction Strategy

The MVP must implement at least one functional extraction strategy.

The implementation may use an LLM-based approach or another suitable extraction mechanism.

The product must not require multiple model providers or extraction backends to demonstrate its core functionality.

The extraction mechanism should remain sufficiently separated from the result contract so that alternative strategies can be introduced later.

#### D. Canonical Output Record Contract

Every extraction result must conform to the canonical output record contract defined in Section 7.

The contract must distinguish:

- Extracted values.
- Extraction confidence.
- Source provenance.
- Validation outcomes.
- Processing status.
- Errors and warnings.

#### E. Field-Level Provenance

The system should associate extracted fields with supporting source evidence wherever available.

At a minimum, the MVP should support:

- Source document identity.
- Source location where available.
- Supporting text or evidence.
- Association between evidence and the extracted field.

If precise source location information is unavailable, the result must represent that limitation honestly.

#### F. Confidence and Uncertainty

The MVP must provide an explicit representation of extraction confidence or uncertainty.

Confidence values must not automatically be interpreted as calibrated probabilities.

The system must not imply that an extraction is reliable merely because a value has been returned.

Missing or ambiguous information must be represented explicitly.

#### G. Validation

The MVP must validate extracted results against the requested schema.

Validation should identify:

- Missing required fields.
- Incorrect data types.
- Invalid structures.
- Basic constraint violations.
- Invalid or incomplete records.

Validation must be independently represented from extraction confidence and source provenance.

#### H. Error and Partial-Result Handling

The system must report:

- Unsupported document formats.
- Document processing failures.
- Extraction failures.
- Missing information.
- Schema validation errors.
- Incomplete extraction results.

Where possible, valid extracted information should remain accessible even when other fields or records fail.

The system must distinguish partial completion from complete success.

#### I. Programmatic Consumption

The primary MVP interface must be usable from Python applications.

A CLI may be included if it can be implemented without compromising the primary library interface.

The output must be machine-readable and consistent across supported extraction strategies.

#### J. Automated Verification

The MVP must include automated tests covering:

- Document processing.
- Schema interpretation.
- Extraction behavior.
- Canonical result construction.
- Provenance association.
- Validation.
- Confidence representation.
- Error handling.
- Partial results.
- Contract consistency.

### 6.3 Explicit MVP Exclusions

The following capabilities are outside the initial MVP:

- OCR.
- Image-based document recognition.
- Advanced table reconstruction.
- Complex visual-layout reconstruction.
- Extensive multilingual document processing.
- Multiple model providers and provider-routing mechanisms.
- Complex fallback and retry orchestration.
- Human-in-the-loop review interfaces.
- Persistent extraction history.
- Database-backed storage.
- Distributed processing.
- Asynchronous batch orchestration.
- Web-based user interfaces.
- Plugin marketplaces.
- Complex semantic or cross-document validation.
- Enterprise workflow automation.
- Advanced confidence calibration.
- RAG pipelines.
- Vector databases and embedding-based indexing.
- Semantic search and document retrieval.
- Searchable document chunk generation.

These exclusions are deliberate boundaries intended to keep the initial implementation focused.

### 6.4 Product Boundaries

structa-maestro is an extraction engine, not a general-purpose document intelligence platform.

Document parsing and preprocessing are permitted when necessary to make document content available for extraction.

However, the project must not evolve into a document ingestion and retrieval system.

Its primary output must remain structured records.

The project should not duplicate the responsibilities of a RAG pipeline, document indexing system, or semantic search engine.

---

## 7. Canonical Output Record Contract

### 7.1 Purpose

The canonical output record contract is one of the central product requirements.

It defines the consistent representation of extraction results regardless of the internal extraction strategy or provider.

The contract must be stable, documented, machine-readable, and independent of any particular extraction implementation.

It is a conceptual contract at the intent stage, not a finalized serialization schema or implementation specification.

### 7.2 Conceptual Result Structure

The following illustrates the expected logical structure:

```
{
  "contract_version": "1.0",
  "extraction_id": "unique-extraction-id",
  "status": "completed",
  "source": {
    "document_id": "document-001",
    "filename": "example.pdf",
    "media_type": "application/pdf"
  },
  "schema": {
    "schema_id": "invoice-schema",
    "schema_version": "1.0"
  },
  "records": [
    {
      "record_id": "record-001",
      "status": "completed",
      "fields": {
        "invoice_number": {
          "value": "INV-2026-001",
          "confidence": {
            "value": 0.96,
            "scale": "0-1",
            "method": "extraction-assessment",
            "calibrated": false
          },
          "provenance": [
            {
              "source_document_id": "document-001",
              "location": {
                "page": 1
              },
              "evidence": "Invoice Number: INV-2026-001",
              "relationship": "direct"
            }
          ],
          "validation": {
            "status": "valid",
            "errors": []
          }
        }
      },
      "validation": {
        "status": "valid",
        "errors": []
      },
      "errors": [],
      "warnings": []
    }
  ],
  "validation": {
    "status": "valid",
    "errors": []
  },
  "errors": [],
  "warnings": []
}
```

This example illustrates the logical contract. It does not prescribe exact class names, internal data structures, serialization libraries, or implementation details.

### 7.3 Contract Components

#### A. Contract Identity

The result must identify the contract version used to produce it.

This enables consumers to understand the result structure and supports future contract evolution.

#### B. Extraction Identity

Every extraction operation must have a distinguishable identifier.

This identifier should allow consumers to associate records, errors, and processing outcomes with a specific extraction operation.

#### C. Processing Status

The result must represent whether processing:

- Completed successfully.
- Completed partially.
- Failed.

Processing status must not be confused with schema validation status.

#### D. Source Identity

The result should identify the originating document.

Where available, source metadata should include:

- Document identifier.
- Filename.
- Media type.

Additional metadata may be supported in future versions.

#### E. Schema Identity

The result must identify the schema against which the information was extracted.

Where available, this should include:

- Schema identifier.
- Schema version.

#### F. Records

The result must support one or more structured records.

Each record must have a distinguishable identity and its own processing and validation information.

A result containing multiple records must not require consumers to infer record boundaries from unstructured text.

#### G. Fields

Each extracted field must represent its value and the relevant quality metadata.

The contract must distinguish a missing value from an explicitly extracted null value where the schema or implementation supports that distinction.

#### H. Confidence

Confidence represents the extraction system's assessment of the reliability of a value.

The contract should provide:

- Confidence value.
- Scale or interpretation.
- Assessment method where known.
- Whether the value has been calibrated.

A confidence score must not be represented as a probability unless its calibration supports that interpretation.

Confidence is not validation.

#### I. Provenance

Provenance describes the relationship between an extracted value and its source evidence.

Where available, it should include:

- Source document identity.
- Source location.
- Supporting evidence.
- Evidence relationship to the extracted value.

The contract should allow multiple pieces of evidence to support one field.

Provenance is not confidence.

#### J. Validation

Validation represents whether an extracted value or record satisfies the applicable schema and constraints.

The contract must distinguish:

- Valid.
- Invalid.
- Not validated.

Validation errors must be represented in a way that allows programmatic inspection.

A valid value may still have low extraction confidence.

#### K. Errors and Warnings

Errors and warnings must be distinguishable.

Errors represent failures or conditions that prevent a requested operation or result from meeting its requirements.

Warnings represent conditions that deserve attention but do not necessarily invalidate the result.

The contract should support errors and warnings at field, record, and result levels where applicable.

### 7.4 Required Contract Invariants

The following principles are mandatory:

1. Every result must identify its contract version.
2. Every extraction operation must have a distinguishable identity.
3. Source identity must be represented whenever available.
4. Schema identity must be represented whenever applicable.
5. Records and fields must have unambiguous boundaries.
6. Confidence must not be treated as validation.
7. Provenance must not be treated as confidence.
8. Validation must not be treated as proof that extracted information is factually correct.
9. Missing information must not be fabricated to satisfy a schema.
10. Partial or failed processing must not be reported as unqualified success.
11. Unsupported metadata must not be invented.
12. Extraction strategies must not expose provider-specific response structures as the public result contract.
13. Contract changes must be explicit and documented.
14. The contract must remain consumable without requiring knowledge of the internal extraction implementation.

### 7.5 Separation of Quality Dimensions

The contract must preserve the following distinctions:

| Dimension         | Question it answers                                             |
| ----------------- | --------------------------------------------------------------- |
| Extracted value   | What information was identified?                                |
| Confidence        | How reliable does the extraction mechanism consider this value? |
| Provenance        | What source evidence supports this value?                       |
| Validation        | Does this value satisfy the expected structure and constraints? |
| Processing status | Did the extraction operation complete successfully?             |

These dimensions are complementary, not interchangeable.

For example:

- A field may be structurally valid but have low extraction confidence.
- A field may have strong supporting evidence but fail schema validation.
- A field may be extracted with high confidence but have incomplete provenance.
- A record may contain valid fields while remaining incomplete overall.
- A document may produce useful partial results even when the overall extraction operation fails.

The contract must be able to represent these situations without misclassification.

---

## 8. Architectural Quality Requirements

The architecture should support a clear separation of responsibilities.

The implementation should be modular enough to allow changes to document processing, extraction strategies, validation, and result construction without unnecessarily affecting unrelated components.

### 8.1 Separation of Responsibilities

The architecture should distinguish the following conceptual responsibilities:

- Document input and identification.
- Document text extraction and preprocessing.
- Schema interpretation.
- Extraction orchestration.
- Extraction strategy execution.
- Provenance association.
- Confidence representation.
- Result construction.
- Schema and constraint validation.
- Error and status reporting.

These are logical responsibilities, not mandatory one-to-one modules or packages.

### 8.2 Explicit Contracts

Important component boundaries must have explicit and understandable contracts.

Components should not depend on undocumented assumptions about internal implementation details.

The public result contract must remain independent of individual extraction strategies.

### 8.3 Controlled Dependencies

Dependencies should be introduced deliberately.

The project should avoid unnecessary coupling to:

- Specific model providers.
- Specific LLM response formats.
- Specific document-processing implementations.
- External orchestration frameworks.
- Unnecessary infrastructure services.

### 8.4 Failure Isolation

Failures in one processing stage should be represented clearly.

Where appropriate, they should not destroy information already extracted successfully.

The system must preserve enough context to explain what failed and where.

### 8.5 Proportional Architecture

The architecture must be appropriate to the project's actual requirements.

Avoid introducing unnecessary abstractions, registries, plugin systems, distributed infrastructure, or complex orchestration mechanisms solely to demonstrate architectural sophistication.

Extensibility must serve a real requirement.

---

## 9. Documentation Requirements

Documentation must accurately reflect the implemented product.

At minimum, the project should provide:

### 9.1 README

The README should explain:

- What structa-maestro does.
- What problem it solves.
- What it does not attempt to solve.
- Supported input formats.
- Core capabilities.
- Installation.
- Basic usage.
- A representative extraction example.
- Output contract overview.
- Current limitations.

### 9.2 Architecture Documentation

Describe the major responsibilities, processing lifecycle, component boundaries, and important architectural decisions.

### 9.3 API Documentation

Document the public Python interfaces, expected inputs, outputs, exceptions, and relevant behavior.

### 9.4 Configuration Documentation

Explain supported configuration options and their expected behavior.

### 9.5 Development Documentation

Include instructions for:

- Setting up the development environment.
- Installing dependencies.
- Running tests.
- Running quality checks.
- Contributing to the project.

### 9.6 Examples

Provide practical examples showing:

- Defining a schema.
- Processing a supported document.
- Extracting structured records.
- Inspecting provenance.
- Interpreting confidence.
- Handling validation errors.
- Handling incomplete results.

### 9.7 Limitations and Change History

Document known limitations and meaningful changes to public behavior or the canonical output contract.

Documentation must not claim capabilities that have not been implemented and verified.

---

## 10. Testing and Verification Requirements

Testing must verify both individual components and the behavior of the complete extraction workflow.

### 10.1 Unit Tests

Cover individual responsibilities, including:

- Input handling.
- Document processing.
- Schema interpretation.
- Extraction result handling.
- Provenance representation.
- Confidence representation.
- Validation.
- Result construction.
- Error classification.

### 10.2 Integration Tests

Verify that the major components work together and produce results conforming to the canonical output record contract.

### 10.3 Contract Tests

Verify that extraction strategies produce compatible public results.

Changes to internal extraction mechanisms must not silently alter the public output structure.

### 10.4 Failure-Mode Tests

Test relevant failure conditions, including:

- Unsupported formats.
- Invalid or unreadable documents.
- Empty documents.
- Missing required information.
- Ambiguous information.
- Extraction failures.
- Invalid data types.
- Schema violations.
- Partial extraction.
- Missing provenance.
- Invalid or unavailable confidence metadata.

### 10.5 Regression Tests

Maintain representative documents and expected extraction outcomes.

Regression tests should detect unintended changes to:

- Extracted values.
- Record structure.
- Field completeness.
- Provenance.
- Validation behavior.
- Processing status.
- Error reporting.

### 10.6 Reproducibility

Where extraction involves nondeterministic components, the project should document the relevant sources of variability.

Evaluation fixtures, expected outputs, and test configuration should be sufficiently controlled to support meaningful comparisons.

### 10.7 Coverage

Aim for a minimum of 80% automated test coverage as an initial engineering target.

Coverage is a quality indicator, not proof of correctness.

Critical behavior and contract invariants must receive direct tests regardless of aggregate coverage.

---

## 11. Extensibility and Compatibility Requirements

The project should permit future evolution without compromising its public contract.

### 11.1 Extraction Strategies

The architecture should allow additional extraction strategies to be introduced without requiring consumers to change how they interpret results.

### 11.2 Provider Independence

Provider independence is desirable where it reduces unnecessary coupling.

However, the MVP does not require multiple providers.

Provider-specific functionality may exist internally, but provider-specific response structures must not leak into the canonical public contract.

### 11.3 Document Processing

Additional document formats and processing capabilities may be introduced in later releases.

New document-processing capabilities must not change the meaning of existing extraction results.

### 11.4 Schema Evolution

The project should support explicit schema identity and versioning.

Schema changes must not silently reinterpret previously produced results.

### 11.5 Contract Evolution

Changes to the canonical output record contract must be deliberate.

Breaking changes must be identifiable and documented.

Backward compatibility should be preserved where practical, with explicit migration behavior when it cannot be maintained.

### 11.6 Avoid Premature Extensibility

Do not implement a complex plugin ecosystem or generic extension framework in the MVP without a demonstrated need.

Prefer clear interfaces and replaceable responsibilities over speculative infrastructure.

---

## 12. Security, Reliability, and Operational Requirements

### 12.1 Input Safety

Documents must be treated as untrusted input.

The system should handle malformed or unexpectedly large documents safely and report processing failures clearly.

### 12.2 Data Integrity

Extracted values must not be silently altered during normalization, validation, or serialization.

Where transformations are necessary, their effects should be predictable and documented.

### 12.3 Sensitive Information

Documents may contain personal, financial, commercial, or confidential information.

The project should avoid unnecessary retention, disclosure, or logging of document contents.

Sensitive source text should not be included in logs by default.

### 12.4 External Services

If an extraction strategy uses an external model provider, users must be able to understand that document content may be transmitted to that provider.

Provider configuration and data-handling implications should be documented.

### 12.5 Error Transparency

Operational and extraction failures must produce actionable information without exposing unnecessary sensitive content.

### 12.6 Reliability

The system should avoid presenting incomplete or invalid results as successful, fully verified output.

The result contract must preserve the distinction between processing completion and data validity.

---

## 13. Health Metrics and Regression Invariants

The project should use measurable indicators to assess whether extraction behavior is improving or deteriorating.

Relevant metrics may include:

### 13.1 Extraction Quality

- Field-level exact-match accuracy where applicable.
- Field-level precision and recall.
- Record-level completeness.
- Extraction failure rate.
- Missing-field rate.
- Incorrect-field rate.

### 13.2 Provenance Quality

- Percentage of extracted fields with provenance.
- Evidence-to-field association accuracy.
- Source-location accuracy where measurable.

### 13.3 Validation Quality

- Schema validation pass rate.
- Invalid type rate.
- Required-field failure rate.
- Constraint violation rate.

### 13.4 Reliability

- Successful processing rate.
- Partial-result rate.
- Failure rate by processing stage.
- Processing time for representative documents.

### 13.5 Regression Invariants

The following must remain true:

- All successful results conform to the canonical output contract.
- Invalid results are not silently reported as valid.
- Missing information is not fabricated.
- Confidence and validation remain separate.
- Provenance and confidence remain separate.
- Partial processing remains distinguishable from complete processing.
- Extraction strategies do not change public result semantics unexpectedly.

Metrics should be introduced in proportion to the capabilities implemented. The MVP should prioritize a small, reproducible evaluation baseline over an extensive evaluation platform.

---

## 14. Constraints

### 14.1 Product Constraints

- The primary product is a structured data extraction engine.
- Structured records are the primary output.
- Document parsing is supporting functionality.
- The project must remain distinct from document ingestion and retrieval systems.
- The MVP must be independently usable as a Python library.

### 14.2 Scope Constraints

The MVP must remain focused on individual-document extraction.

Advanced processing workflows must not become prerequisites for delivering the core product.

### 14.3 Technical Constraints

Technology choices should be justified by functional requirements, maintainability, and practical implementation needs.

The intent does not mandate a particular LLM provider, document-processing library, validation framework, or orchestration technology.

### 14.4 Resource Constraints

The initial implementation should avoid unnecessary infrastructure and operational complexity.

The project should be runnable in a conventional Python development environment without requiring distributed services.

### 14.5 Naming Constraints

The project and repository name is `structa-maestro`.

The Python import package should use the valid Python identifier `structa_maestro`.

The project distribution name and import package name should be documented consistently.

---

## 15. Edge Cases and Failure Modes

The system must explicitly consider the following situations.

### 15.1 Document-Level Issues

- Empty document.
- Corrupted or unreadable document.
- Unsupported format.
- Document containing no extractable text.
- Document with unexpectedly large content.
- Document with incomplete or inconsistent metadata.

### 15.2 Extraction Issues

- Requested information does not exist.
- Requested information is ambiguous.
- Multiple candidate values exist.
- Extracted value is incomplete.
- Extraction mechanism returns malformed output.
- Extraction mechanism fails during processing.
- Extraction produces values unsupported by source evidence.

### 15.3 Schema Issues

- Invalid schema definition.
- Missing required field.
- Unexpected field.
- Incorrect data type.
- Invalid nested structure.
- Constraint violation.

### 15.4 Provenance Issues

- Evidence cannot be located.
- Source location is unavailable.
- Multiple evidence passages support a value.
- Evidence conflicts with the extracted value.
- Evidence is insufficient to support a definitive extraction.

### 15.5 Confidence Issues

- Confidence is unavailable.
- Confidence cannot be interpreted consistently.
- Confidence is uncalibrated.
- Confidence is high despite weak evidence.
- Confidence differs across extraction strategies.

### 15.6 Partial Results

The system must be able to represent situations where:

- Some fields are extracted successfully while others are missing.
- Some records are valid while others are invalid.
- Extraction completes but validation fails.
- Document processing succeeds but extraction fails.
- Extraction succeeds but provenance is incomplete.

These conditions must be reflected in the result status and associated errors or warnings.

---

## 16. Verification Signals and Acceptance Evidence

The MVP should be considered functionally complete when the following evidence exists.

### 16.1 Core Workflow

A developer can provide a supported document and a user-defined schema and obtain structured extraction results through the Python interface.

### 16.2 Supported Formats

TXT, text-based PDF, and DOCX documents can be processed through the supported workflow.

Unsupported or unreadable documents produce explicit errors.

### 16.3 Schema Compliance

Extracted results are checked against the supplied schema.

Invalid values and missing required fields are reported.

### 16.4 Canonical Contract

All extraction results conform to the canonical output record contract.

The contract is documented and covered by automated tests.

### 16.5 Provenance

Extracted fields can be associated with source evidence wherever available.

Unavailable provenance is represented honestly.

### 16.6 Confidence

Confidence and uncertainty are represented separately from validation.

Uncalibrated scores are not described as probabilities.

### 16.7 Partial Results

The engine can preserve and expose successfully extracted information when other fields or records fail.

### 16.8 Error Handling

Failures are distinguishable, actionable, and represented in the result contract.

### 16.9 Testing

Automated tests cover the major processing responsibilities, contract invariants, integration behavior, and representative failure conditions.

### 16.10 Documentation

A developer unfamiliar with the project can understand its purpose, install it, execute a representative extraction, inspect the output, and understand its limitations using the documentation.

### 16.11 Scope Integrity

The MVP delivers structured extraction without introducing RAG, vector indexing, semantic retrieval, or searchable document chunk generation as core capabilities.

---

## 17. Assumptions and Open Questions

The following assumptions guide the initial project definition.

### 17.1 Assumptions

- The initial target users are developers.
- Python is the primary implementation and consumption language.
- Individual-document processing is sufficient for the MVP.
- TXT, text-based PDF, and DOCX provide an appropriate initial format set.
- At least one extraction strategy is sufficient to demonstrate the product.
- A canonical result contract is necessary for consistent downstream consumption.
- Provenance, confidence, and validation must remain separate dimensions.
- A database is not required for the MVP.
- A web interface is not required for the MVP.
- OCR is not required for the MVP.
- A CLI is optional; the Python library interface is the priority.

### 17.2 Open Questions

The following decisions may be resolved during specification and planning:

1. Which schema representation should be adopted for the initial implementation?
2. Which extraction strategy best balances reliability, implementation complexity, and reproducibility?
3. Which document-processing libraries are most appropriate for the supported formats?
4. What is the minimum useful granularity for source locations and evidence?
5. Which confidence representation can be implemented honestly and consistently?
6. Which validation constraints should be supported in the MVP beyond basic structural validation?
7. Should the initial release include a CLI, or should it remain exclusively a Python library?
8. What representative document fixtures and evaluation criteria should form the initial regression baseline?

These questions should be resolved through explicit technical decisions rather than hidden assumptions.

The specification and implementation plan should preserve the distinction between confirmed requirements and decisions that remain open.

---

## 18. Intent Summary

structa-maestro is a reusable Python structured data extraction engine.

Its purpose is to transform unstructured documents into schema-conforming, machine-readable records while preserving information about extracted values, source provenance, confidence, validation, processing status, and errors.

The MVP must establish a complete individual-document extraction workflow supporting TXT, text-based PDF, and DOCX; user-defined schemas; at least one extraction strategy; a canonical output record contract; field-level provenance where available; explicit uncertainty; validation; partial-result handling; and automated verification.

The project must maintain clear architectural boundaries, accurate documentation, reproducible tests, controlled dependencies, and a stable public result contract.

Its primary output is structured data.

Document parsing and preprocessing are supporting capabilities. RAG, vector indexing, semantic search, document retrieval, and searchable chunk generation are explicitly outside the product's core scope.

Future capabilities may expand extraction strategies, document support, evaluation, and workflow automation without compromising the product's identity or the integrity of its canonical output contract.

---

**Intent artifact — for `/speckit.specify`.**

This document defines product intent and scope. It does not prescribe a complete implementation architecture, technology stack, or task breakdown. Those decisions belong to the specification and subsequent planning stages.

**Naming note:** The artifact uses `structa-maestro` as the project/distribution name and `structa_maestro` only where the Python import identifier is relevant. PyPI normalizes hyphens and underscores to the same distribution identity.
