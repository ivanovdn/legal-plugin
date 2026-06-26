# ingest/chunk_models.py
from pydantic import BaseModel


class LegalChunk(BaseModel):
    chunk_id: str
    doc_id: str
    doc_title: str
    doc_filename: str
    doc_type: str       # contract | legislation | template | policy | case_law | msa
    client_id: str      # "internal" for shared docs
    jurisdiction: str
    sensitivity: str    # confidential | internal | public
    section: str = ""
    section_number: str = ""
    clause: str = ""
    clause_number: str = ""
    clause_type: str = ""
    section_display: str = ""
    text: str
    char_count: int = 0
    chunk_index: int = 0
    chunk_strategy: str = ""  # clause-level | section-based | template-aware
    last_updated: str = ""
