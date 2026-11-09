"""Strict schema for Stage 3b records. See specs/design.md section 7."""

import re
from datetime import date
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

T = TypeVar("T")
Confidence = Literal["high", "medium", "low"]
ManagementType = Literal["WEG", "Miet", "SE", "Gewerbe"]
MAX_QUOTE = 200


class Field(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")

    value: T | None = None
    source_url: str | None = None
    evidence_quote: str | None = None
    confidence: Confidence | None = None

    @model_validator(mode="after")
    def evidence_matches_value(self):
        if self.value is None:
            if self.source_url or self.evidence_quote or self.confidence:
                raise ValueError("a null value must have no source_url, evidence_quote or confidence")
        elif not (self.source_url and self.evidence_quote and self.confidence):
            raise ValueError("a non-null value needs source_url, evidence_quote and confidence")
        return self

    @field_validator("evidence_quote")
    @classmethod
    def quote_is_short(cls, quote):
        if quote is not None and len(quote) > MAX_QUOTE:
            raise ValueError(f"evidence_quote is longer than {MAX_QUOTE} characters")
        return quote

    @field_validator("source_url")
    @classmethod
    def url_is_http(cls, url):
        if url is not None and not url.startswith(("http://", "https://")):
            raise ValueError("source_url must be http(s)")
        return url


class CompanyExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    units_managed: Field[int]
    management_types: Field[list[ManagementType]]
    employees: Field[int]
    founding_year: Field[int]
    other_cities: Field[list[str]]
    digital_maturity: Field[Literal["high", "medium", "low"]]
    company_summary: Field[str]

    @field_validator("units_managed")
    @classmethod
    def units_plausible(cls, f):
        if f.value is not None and not 1 <= f.value <= 500_000:
            raise ValueError("units_managed outside 1-500000")
        return f

    @field_validator("employees")
    @classmethod
    def employees_plausible(cls, f):
        if f.value is not None and not 1 <= f.value <= 20_000:
            raise ValueError("employees outside 1-20000")
        return f

    @field_validator("founding_year")
    @classmethod
    def year_plausible(cls, f):
        if f.value is not None and not 1850 <= f.value <= date.today().year:
            raise ValueError("founding_year implausible")
        return f

    @field_validator("company_summary")
    @classmethod
    def summary_short(cls, f):
        if f.value is not None and len(f.value) > 200:
            raise ValueError("company_summary longer than 200 characters")
        return f


FIELDS = ["units_managed", "management_types", "employees", "founding_year",
          "other_cities", "digital_maturity", "company_summary"]
# A summary is written by the extractor, so it has an evidence quote but its
# own text cannot be expected to appear verbatim in the page.


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def missing_quotes(record: CompanyExtraction, page_text: str) -> list[str]:
    """Return field names whose evidence_quote does not occur in the page text.

    This catches invented values mechanically.
    """
    haystack = _squash(page_text)
    missing = []
    for name in FIELDS:
        quote = getattr(record, name).evidence_quote
        # " … " joins separate verbatim fragments, e.g. one per management type.
        if quote and any(_squash(part) not in haystack for part in quote.split(" … ")):
            missing.append(name)
    return missing


class ExtractionBackend:
    """Interface for Stage 3b backends. Only claude_code exists today."""

    name = "base"

    def prepare_batch(self, company_ids: list[str]) -> None:
        raise NotImplementedError
