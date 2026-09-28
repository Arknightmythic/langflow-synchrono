# AI Reasoning Evaluation Rubric

Rubric for evaluating the output of the Synchrono AI Reasoning Engine using LLM-as-a-Judge.
Every criterion evaluates the generated explanation against the authoritative field comparison verdicts and ground-truth record values.

---

## 1. System Under Test

- **Name**: Synchrono AI Reasoning Engine (Gemma 3:12B + DuckDB Hybrid Cache)
- **Input Type**: Pre-computed field comparison verdicts (SAME, DIFFERENT, EMPTY_IN_*) + Raw record values (Institution vs Master)
- **Output Type**: Structured English explanation and template (`{incoming.<field>}` vs `{master.<field>}`)
- **Deployment Context**: High-throughput production pipeline for record deduplication and manual identity review

---

## 2. Evaluation Criteria

### Criterion 1: Factuality and Field Fidelity (`factuality_fidelity`)

- **Definition**: Checks whether the explanation faithfully reports every discordant field from the authoritative comparison without hallucinating differences on matching fields or inventing non-existent values.
- **Level**: Trajectory / Record
- **Scoring**: `3-point` (pass / partial / fail)
- **Scale Anchors**:
  - `pass`: Every discordant field is accurately reported; no field marked SAME is reported as different; quoted values match record inputs.
  - `partial`: Reports the primary discordances correctly, but omits one minor discrepancy or has minor quotation formatting variance.
  - `fail`: Claims a field is different when it is marked SAME, completely hallucinates values, or fails to report any actual discrepancy.
- **Failure Modes Caught**:
  - Hallucinating discrepancy on identical NIK or identical names.
  - Swapping institution and master field values.
  - Reporting empty fields as matching.
- **Bias Risks**: Length bias (longer explanations must not be rated higher if they contain hallucinations).

---

### Criterion 2: Template Structure and Placeholder Consistency (`template_consistency`)

- **Definition**: Checks whether the template abstraction correctly uses the standard `{incoming.<field>}` and `{master.<field>}` placeholder syntax to enable dynamic bulk SQL hydration.
- **Level**: Record / Template
- **Scoring**: `binary` (pass / fail)
- **Scale Anchors**:
  - `pass`: All dynamic values in the explanation correspond to valid placeholders `{incoming.<field>}` and `{master.<field>}`; brackets are balanced and syntax is valid for SQL replacement.
  - `fail`: Raw record values are hardcoded in the template; invalid placeholder tokens exist; unbalanced braces; or template fails SQL hydration.
- **Failure Modes Caught**:
  - Hardcoded record values saved into `reasoning_patterns`.
  - Non-standard tokens like `<incoming_name>` or `[master.dob]`.
  - Unclosed curly braces `{{incoming.nama_lengkap}`.

---

### Criterion 3: Conciseness and Reviewer Readability (`conciseness_readability`)

- **Definition**: Checks whether the narrative is direct, concise (1 to 2 sentences), free of generic conversational filler, and readable at a glance by a human manual review operator.
- **Level**: Record / Output
- **Scoring**: `3-point` (pass / partial / fail)
- **Scale Anchors**:
  - `pass`: Direct, informative explanation in 1 to 2 sentences without conversational preamble (no "Sure, here is the explanation" or "Based on the analysis").
  - `partial`: Explains the difference correctly but is overly verbose (3 or more sentences) or contains slight conversational padding.
  - `fail`: Paragraph-length essay, robotic disclaimers, confusing grammar, or unintelligible phrasing.
- **Failure Modes Caught**:
  - Generic LLM conversational fluff ("I have analyzed the two records and concluded that...").
  - Repetitive phrases.
  - Incomplete sentences or raw JSON dumps.

---

## 3. Aggregation Strategy

- **Conjunctive Gate**: For production quality gate, a template must score:
  - `factuality_fidelity`: **pass**
  - `template_consistency`: **pass**
  - `conciseness_readability`: **pass** or **partial**
- If `factuality_fidelity` or `template_consistency` fails, the output is rejected and falls back to deterministic rule generation.
