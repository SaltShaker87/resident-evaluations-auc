# RAG + Ontology Layer

This folder holds the local Retrieval-Augmented Generation (RAG) and ontology
resources used by the resident summary generator.

**Everything in this folder stays local on this machine.** No data here is sent to
the cloud or to any third-party service.

## Folders

- **`documents/`** — Reference material in Markdown form. Holds the two ACGME
  reference files (`acgme_im_milestones.md` and `acgme_im_supplemental_guide.md`)
  that the generator retrieves context from.

- **`ontology/`** — The ACGME ontology in JSON form. Defines the structured
  concepts (competencies, milestones, and how they relate) used to organize and
  ground the retrieved material.
